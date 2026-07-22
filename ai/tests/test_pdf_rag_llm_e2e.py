from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from uuid import UUID, uuid4

import fitz
import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from rag_service.retrieval import psycopg_database_url


RAG_E2E_ENABLED = (
    os.getenv("RUN_DB_INTEGRATION") == "1"
    and os.getenv("ALLOW_TEST_DB_MUTATION") == "1"
    and os.getenv("RUN_RAG_E2E") == "1"
)

pytestmark = pytest.mark.skipif(
    not RAG_E2E_ENABLED,
    reason="Set RUN_DB_INTEGRATION=1, ALLOW_TEST_DB_MUTATION=1, and RUN_RAG_E2E=1.",
)


def _assert_test_database(database_url: str) -> None:
    database_name = database_url.split("?", 1)[0].rsplit("/", 1)[-1]
    if not database_name.endswith("_test"):
        raise RuntimeError("PDF RAG E2E requires a database ending in '_test'.")


def _create_pdf(path: Path, *, heading: str, lines: list[str]) -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 86), heading, fontsize=16, fontname="hebo")
    y = 126
    for line in lines:
        page.insert_text((72, y), line, fontsize=11, fontname="helv")
        y += 24
    pdf.save(path)
    pdf.close()


def _register(client: httpx.Client, employee_number: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "employee_number": employee_number,
            "name": "PDF RAG E2E document manager",
            "password": password,
        },
    )
    assert response.status_code == 201, response.text


def _login(client: httpx.Client, employee_number: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"employee_number": employee_number, "password": password},
    )
    assert response.status_code == 200, response.text
    token = response.json().get("access_token")
    assert isinstance(token, str) and token
    return token


def _assign_role(database_url: str, employee_number: str, role_code: str) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_roles (user_id, role_id)
                SELECT u.id, r.id
                FROM users u
                JOIN roles r ON r.code = %s AND r.is_active = true
                WHERE u.employee_number = %s
                ON CONFLICT (user_id, role_id) DO NOTHING
                """,
                (role_code, employee_number),
            )
        connection.commit()


def _upload(
    client: httpx.Client,
    *,
    token: str | None,
    pdf_path: Path,
    model_name: str,
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with pdf_path.open("rb") as pdf_file:
        response = client.post(
            "/api/v1/documents/upload",
            headers=headers,
            data={
                "product_type": "light curtain" if "LC" in model_name else "conveyor",
                "model_name": model_name,
                "manufacturer": "SafeMaint E2E",
                "document_type_code": "equipment_manual",
                "source_type": "manual",
                "access_level": "restricted",
            },
            files={"file": (pdf_path.name, pdf_file, "application/pdf")},
        )
    return {"status_code": response.status_code, "body": response.json()}


def _insert_public_fixture(
    database_url: str,
    *,
    pdf_path: Path,
    storage_dir: Path,
    run_id: str,
) -> tuple[UUID, UUID, Path]:
    document_id = uuid4()
    version_id = uuid4()
    stored_path = storage_dir / f"{uuid4().hex}.pdf"
    shutil.copyfile(pdf_path, stored_path)
    content = stored_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    metadata = {
        "manufacturer": "SafeMaint public E2E",
        "model_number": "TEST-LC-100-PUBLIC",
        "product_type": "light curtain",
        "e2e_run_id": run_id,
    }
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents
                    (id, external_id, title, source_type, document_type_code,
                     lifecycle_status, access_level, metadata)
                VALUES (%s, %s, %s, 'guide', 'public_guide', 'pending',
                        'public', %s)
                """,
                (
                    document_id,
                    f"e2e-public:{run_id}:{document_id}",
                    pdf_path.stem,
                    Jsonb(metadata),
                ),
            )
            cursor.execute(
                """
                INSERT INTO document_versions
                    (id, document_id, version_number, original_filename,
                     stored_filename, storage_path, sha256, file_size,
                     mime_type, status)
                VALUES (%s, %s, 1, %s, %s, %s, %s, %s,
                        'application/pdf', 'pending')
                """,
                (
                    version_id,
                    document_id,
                    pdf_path.name,
                    stored_path.name,
                    str(stored_path),
                    digest,
                    len(content),
                ),
            )
            cursor.execute(
                """
                INSERT INTO document_processing_jobs (document_version_id, status)
                VALUES (%s, 'queued')
                """,
                (version_id,),
            )
        connection.commit()
    return document_id, version_id, stored_path


def _wait_for_worker(
    database_url: str,
    version_ids: list[UUID],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT dv.id, dv.status, j.status, j.attempts, j.error_message
                    FROM document_versions dv
                    JOIN document_processing_jobs j
                      ON j.document_version_id = dv.id
                    WHERE dv.id = ANY(%s::uuid[])
                    ORDER BY dv.id
                    """,
                    (version_ids,),
                )
                rows = cursor.fetchall()
        if len(rows) == len(version_ids):
            failed = [row for row in rows if row[2] == "failed"]
            if failed:
                pytest.fail(f"Worker failed: {failed}")
            if all(row[1] == "review_required" and row[2] == "completed" for row in rows):
                return
        time.sleep(2)
    pytest.fail("Worker did not complete all PDF jobs before the E2E timeout.")


def _approve(
    client: httpx.Client,
    token: str,
    document_id: UUID,
    version_id: UUID,
) -> dict:
    response = client.post(
        f"/api/v1/documents/{document_id}/versions/{version_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    return {"status_code": response.status_code, "body": response.json() if response.content else {}}


def _rag_search(rag_url: str, payload: dict) -> dict:
    response = httpx.post(f"{rag_url}/v1/chat", json=payload, timeout=300)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def pdf_rag_environment(tmp_path_factory: pytest.TempPathFactory):
    database_url = psycopg_database_url(os.environ["DATABASE_URL"])
    _assert_test_database(database_url)
    password = os.environ.get("E2E_USER_PASSWORD", "")
    if not password:
        pytest.fail("E2E_USER_PASSWORD is required and must be ephemeral.")
    run_id = os.environ.get("E2E_RUN_ID", uuid4().hex[:12])[:12]
    employee_number = f"E2E_DOC_MANAGER_{run_id}".upper()[:30]
    backend_url = os.getenv("BACKEND_E2E_URL", "http://backend:8000")
    rag_url = os.getenv("RAG_E2E_URL", "http://rag:8010")
    timeout_seconds = float(os.getenv("RAG_E2E_TIMEOUT_SECONDS", "1200"))
    storage_dir = Path(os.getenv("DOCUMENT_STORAGE_DIR", "/data/documents"))
    storage_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = tmp_path_factory.mktemp("pdf-rag-e2e")

    company_lc = temp_dir / "TEST-LC-100-COMPANY.pdf"
    company_cv = temp_dir / "TEST-CV-200-COMPANY.pdf"
    public_lc = temp_dir / "TEST-LC-100-PUBLIC.pdf"
    _create_pdf(
        company_lc,
        heading="INSPECTION PROCEDURE",
        lines=[
            "SafeMaint company E2E test document.",
            "Equipment: TEST-LC-100 light curtain.",
            "Before inspection, isolate the main power and apply lockout/tagout.",
            "Verify alignment between the emitter and receiver.",
            "The test inspection interval is E2E-37 days.",
            "Do not use this document for real work.",
        ],
    )
    _create_pdf(
        company_cv,
        heading="CLEANING PROCEDURE",
        lines=[
            "SafeMaint company E2E decoy document.",
            "Equipment: TEST-CV-200 conveyor.",
            "Stop the conveyor before belt cleaning.",
            "The test inspection interval is E2E-99 days.",
            "Do not use this document for real work.",
        ],
    )
    _create_pdf(
        public_lc,
        heading="PUBLIC INSPECTION PROCEDURE",
        lines=[
            "SafeMaint public E2E test document.",
            "Equipment: TEST-LC-100 light curtain.",
            "Before inspection, isolate the main power and apply lockout/tagout.",
            "Verify alignment between the emitter and receiver.",
            "The test inspection interval is E2E-37 days.",
            "Do not use this document for real work.",
        ],
    )
    for path in (company_lc, company_cv, public_lc):
        with fitz.open(path) as pdf:
            assert pdf.page_count == 1
            assert "E2E-" in "".join(page.get_text() for page in pdf)

    tracked_document_ids: list[UUID] = []
    tracked_paths: list[Path] = []
    with httpx.Client(base_url=backend_url, timeout=60) as client:
        _register(client, employee_number, password)
        worker_token = _login(client, employee_number, password)

        assert _upload(
            client,
            token=None,
            pdf_path=company_lc,
            model_name=f"TEST-LC-100-{run_id}",
        )["status_code"] == 401
        assert _upload(
            client,
            token=worker_token,
            pdf_path=company_lc,
            model_name=f"TEST-LC-100-{run_id}",
        )["status_code"] == 403

        _assign_role(database_url, employee_number, "document_manager")
        manager_token = _login(client, employee_number, password)
        headers = {"Authorization": f"Bearer {manager_token}"}
        public_spoof = client.post(
            "/api/v1/documents/upload",
            headers=headers,
            data={
                "product_type": "light curtain",
                "model_name": f"TEST-LC-100-{run_id}",
                "manufacturer": "SafeMaint E2E",
                "document_type_code": "public_guide",
                "source_type": "guide",
                "access_level": "restricted",
            },
            files={"file": (company_lc.name, company_lc.read_bytes(), "application/pdf")},
        )
        assert public_spoof.status_code == 422

        first_lc = _upload(
            client,
            token=manager_token,
            pdf_path=company_lc,
            model_name=f"TEST-LC-100-{run_id}",
        )
        second_lc = _upload(
            client,
            token=manager_token,
            pdf_path=company_lc,
            model_name=f"TEST-LC-100-{run_id}",
        )
        conveyor = _upload(
            client,
            token=manager_token,
            pdf_path=company_cv,
            model_name=f"TEST-CV-200-{run_id}",
        )
        for result in (first_lc, second_lc, conveyor):
            assert result["status_code"] == 201, result["body"]
            assert result["body"]["status"] == "pending"
        assert first_lc["body"]["version_number"] == 1
        assert second_lc["body"]["version_number"] == 2

        lc_document_id = UUID(first_lc["body"]["document_id"])
        lc_v1 = UUID(first_lc["body"]["document_version_id"])
        lc_v2 = UUID(second_lc["body"]["document_version_id"])
        cv_document_id = UUID(conveyor["body"]["document_id"])
        cv_version_id = UUID(conveyor["body"]["document_version_id"])
        tracked_document_ids.extend((lc_document_id, cv_document_id))

        public_document_id, public_version_id, public_path = _insert_public_fixture(
            database_url,
            pdf_path=public_lc,
            storage_dir=storage_dir,
            run_id=run_id,
        )
        tracked_document_ids.append(public_document_id)
        tracked_paths.append(public_path)

        all_versions = [lc_v1, lc_v2, cv_version_id, public_version_id]
        _wait_for_worker(
            database_url,
            all_versions,
            timeout_seconds=timeout_seconds,
        )

        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT dv.id, dv.storage_path, dv.sha256, j.status, j.attempts,
                           j.error_message, dv.processing_metadata,
                           dv.failure_reason, dv.page_count
                    FROM document_versions dv
                    JOIN document_processing_jobs j
                      ON j.document_version_id = dv.id
                    WHERE dv.id = ANY(%s::uuid[])
                    """,
                    (all_versions,),
                )
                version_rows = cursor.fetchall()
                tracked_paths.extend(Path(row[1]) for row in version_rows)
                assert all(len(row[2]) == 64 for row in version_rows)
                assert all(row[3] == "completed" for row in version_rows)
                assert all(1 <= row[4] <= 3 for row in version_rows)
                assert all(row[5] is None for row in version_rows)
                assert all(row[6].get("extractor") == "docling" for row in version_rows)
                assert all(
                    row[6].get("extractor_version") == "2.113.0"
                    for row in version_rows
                )
                assert all(row[6].get("fallback_used") is False for row in version_rows)
                assert all(isinstance(row[6].get("ocr_used"), bool) for row in version_rows)
                assert all(row[7] is None for row in version_rows)
                assert all(row[8] == 1 for row in version_rows)
                cursor.execute(
                    """
                    SELECT dc.document_version_id, dc.content, dc.content_hash,
                           dc.page_start, dc.page_end, dc.section_path,
                           dc.metadata, dc.embedding_status, dc.embedding_model,
                           dc.embedding_dimension
                    FROM document_chunks dc
                    WHERE dc.document_version_id = ANY(%s::uuid[])
                    ORDER BY dc.document_version_id, dc.chunk_index
                    """,
                    (all_versions,),
                )
                chunk_rows = cursor.fetchall()
        assert chunk_rows
        assert all(row[2] and len(row[2]) == 64 for row in chunk_rows)
        assert all(row[3] is not None and row[4] is not None for row in chunk_rows)
        assert all(row[5] for row in chunk_rows)
        assert all(row[7] == "ready" for row in chunk_rows)
        assert all(row[8] == "BAAI/bge-m3" for row in chunk_rows)
        assert all(row[9] == 1024 for row in chunk_rows)
        assert all(
            row[6].get("manufacturer")
            and row[6].get("product_type")
            and row[6].get("model_name")
            for row in chunk_rows
        )
        lc_content = " ".join(row[1] for row in chunk_rows if row[0] in {lc_v1, lc_v2})
        cv_content = " ".join(row[1] for row in chunk_rows if row[0] == cv_version_id)
        assert "TEST-LC-100" in lc_content and "E2E-37" in lc_content
        assert "TEST-CV-200" in cv_content and "E2E-99" in cv_content

        preapproval = _rag_search(
            rag_url,
            {
                "question": "TEST-LC-100 light curtain inspection interval",
                "context": {
                    "selected_document_ids": [str(lc_document_id)],
                    "selected_document_version_ids": [str(lc_v1)],
                },
                "analysis": {"search_keywords": ["TEST-LC-100", "E2E-37"]},
                "access_scope": {"allow_company": True, "all_sites": True},
            },
        )
        assert preapproval["sources"] == []

        assert _approve(client, manager_token, lc_document_id, lc_v1)["status_code"] == 200
        old_selection_before_replacement = _rag_search(
            rag_url,
            {
                "question": "TEST-LC-100 light curtain inspection interval",
                "context": {
                    "selected_document_ids": [str(lc_document_id)],
                    "selected_document_version_ids": [str(lc_v2)],
                },
                "analysis": {"search_keywords": ["TEST-LC-100", "E2E-37"]},
                "access_scope": {"allow_company": True, "all_sites": True},
            },
        )
        assert old_selection_before_replacement["sources"] == []

        assert _approve(client, manager_token, lc_document_id, lc_v2)["status_code"] == 200
        assert _approve(client, manager_token, cv_document_id, cv_version_id)["status_code"] == 200
        assert _approve(client, manager_token, public_document_id, public_version_id)["status_code"] == 200

        old_version = _rag_search(
            rag_url,
            {
                "question": "TEST-LC-100 light curtain inspection interval",
                "context": {
                    "selected_document_ids": [str(lc_document_id)],
                    "selected_document_version_ids": [str(lc_v1)],
                },
                "analysis": {"search_keywords": ["TEST-LC-100", "E2E-37"]},
                "access_scope": {"allow_company": True, "all_sites": True},
            },
        )
        assert old_version["sources"] == []

        yield {
            "client": client,
            "manager_token": manager_token,
            "rag_url": rag_url,
            "lc_document_id": lc_document_id,
            "lc_version_id": lc_v2,
            "cv_document_id": cv_document_id,
            "cv_version_id": cv_version_id,
            "public_document_id": public_document_id,
            "public_version_id": public_version_id,
        }

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM audit_events WHERE entity_id = ANY(%s::uuid[])",
                (tracked_document_ids,),
            )
            cursor.execute(
                "DELETE FROM documents WHERE id = ANY(%s::uuid[])",
                (tracked_document_ids,),
            )
            cursor.execute(
                "DELETE FROM audit_events WHERE actor_user_id IN "
                "(SELECT id FROM users WHERE employee_number = %s)",
                (employee_number,),
            )
            cursor.execute(
                "DELETE FROM user_roles WHERE user_id IN "
                "(SELECT id FROM users WHERE employee_number = %s)",
                (employee_number,),
            )
            cursor.execute(
                "DELETE FROM users WHERE employee_number = %s",
                (employee_number,),
            )
        connection.commit()
    for path in set(tracked_paths):
        path.unlink(missing_ok=True)


def test_company_pdf_worker_hybrid_rag_and_backend_scope(pdf_rag_environment) -> None:
    state = pdf_rag_environment
    selected_payload = {
        "question": "What is the TEST-LC-100 light curtain test inspection interval?",
        "context": {
            "equipment_name": "TEST-LC-100 light curtain",
            "model_number": "TEST-LC-100",
            "selected_document_ids": [str(state["lc_document_id"])],
            "selected_document_version_ids": [str(state["lc_version_id"])],
        },
        "analysis": {"search_keywords": ["TEST-LC-100", "light curtain", "E2E-37"]},
        "access_scope": {"allow_company": True, "all_sites": True},
    }
    rag_response = _rag_search(state["rag_url"], selected_payload)
    assert rag_response["retrieval_mode"] == "hybrid"
    assert rag_response["sources"]
    top = rag_response["sources"][0]
    assert top["document_id"] == str(state["lc_document_id"])
    assert top["document_version"] == 2
    assert top["original_filename"] == "TEST-LC-100-COMPANY.pdf"
    assert top["page_start"] == 1
    assert top["section"]
    assert "E2E-37" in top["excerpt"]
    assert top["similarity"] is not None
    assert top["retrieval_score"] is not None
    assert top["reranker_score"] is not None
    assert "E2E-99" not in rag_response["answer"]

    unselected = _rag_search(
        state["rag_url"],
        {
            **selected_payload,
            "context": {
                "equipment_name": "TEST-LC-100 light curtain",
                "model_number": "TEST-LC-100",
            },
        },
    )
    assert unselected["sources"][0]["document_id"] == str(state["lc_document_id"])
    assert unselected["sources"][0]["document_id"] != str(state["cv_document_id"])

    company_disabled = _rag_search(
        state["rag_url"],
        {**selected_payload, "access_scope": {"allow_company": False}},
    )
    assert company_disabled["sources"] == []

    wrong_document = _rag_search(
        state["rag_url"],
        {
            **selected_payload,
            "context": {
                "equipment_name": "TEST-LC-100 light curtain",
                "selected_document_ids": [str(state["cv_document_id"])],
                "selected_document_version_ids": [str(state["cv_version_id"])],
            },
        },
    )
    assert all(
        source["document_id"] != str(state["lc_document_id"])
        for source in wrong_document["sources"]
    )

    anonymous = state["client"].post(
        "/api/v1/chat",
        json={key: value for key, value in selected_payload.items() if key != "access_scope"},
    )
    assert anonymous.status_code == 200
    assert all(
        source.get("document_scope") != "company"
        for source in anonymous.json()["sources"]
    )

    authenticated = state["client"].post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {state['manager_token']}"},
        json={key: value for key, value in selected_payload.items() if key != "access_scope"},
    )
    assert authenticated.status_code == 200
    authenticated_body = authenticated.json()
    assert authenticated_body["generation_mode"] == "template"
    assert authenticated_body["sources"][0]["document_id"] == str(state["lc_document_id"])
    if os.getenv("ALLOW_EXTERNAL_LLM", "false").lower() == "true":
        assert "외부 LLM" in (authenticated_body.get("warning") or "")

    unsupported = _rag_search(
        state["rag_url"],
        {
            **selected_payload,
            "question": "What hydraulic pressure in bar is specified for TEST-LC-100?",
            "analysis": {"search_keywords": ["TEST-LC-100", "hydraulic", "pressure", "bar"]},
        },
    )
    assert "E2E-99" not in unsupported["answer"]
    assert re.search(r"\b\d+(?:\.\d+)?\s*bar\b", unsupported["answer"], re.I) is None


def test_public_pdf_external_llm_grounded_answer(pdf_rag_environment) -> None:
    if os.getenv("RUN_EXTERNAL_LLM_E2E") != "1" or not os.getenv("OPENAI_API_KEY"):
        pytest.skip("External LLM E2E requires an explicit flag and OPENAI_API_KEY.")

    state = pdf_rag_environment
    payload = {
        "question": "What is the TEST-LC-100 test inspection interval?",
        "context": {
            "equipment_name": "TEST-LC-100 light curtain",
            "selected_document_ids": [str(state["public_document_id"])],
            "selected_document_version_ids": [str(state["public_version_id"])],
        },
        "analysis": {"search_keywords": ["TEST-LC-100", "E2E-37"]},
    }
    response = state["client"].post("/api/v1/chat", json=payload, timeout=300)
    assert response.status_code == 200
    body = response.json()
    assert body["sources"]
    assert body["sources"][0]["document_id"] == str(state["public_document_id"])
    assert body["generation_mode"] == "openai"
    assert body["model"] == os.getenv("LLM_ANSWER_MODEL", "gpt-4o-mini")
    assert "E2E-37" in body["answer"]
    assert "E2E-99" not in body["answer"]
    assert body["sources"][0]["page_start"] == 1
    assert body["sources"][0]["section"]

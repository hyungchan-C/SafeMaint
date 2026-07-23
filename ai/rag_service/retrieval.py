from __future__ import annotations

import re
from collections.abc import Sequence
from threading import Lock
from typing import Any
from uuid import UUID

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from rag_service.config import Settings
from rag_service.schemas import ChatRequest, ChatSource, InternalChatRequest


class RetrievalError(RuntimeError):
    """Raised when the model or vector database cannot produce grounded results."""


GENERIC_QUERY_TERMS = frozenset(
    {
        "how",
        "method",
        "please",
        "safety",
        "work",
        "작업",
        "방법",
        "안전",
        "관련",
        "문서",
        "요약",
        "요약해",
        "요약해줘",
        "정리",
        "정리해",
        "정리해줘",
        "내용",
        "파일",
        "첨부",
        "첨부한",
        "확인",
        "확인해줘",
        "확인해",
        "교체",
        "교체법",
        "교체작업",
        "교체할",
        "청소",
        "청소법",
        "청소할",
        "점검",
        "점검할",
        "정비",
        "설치",
        "설치법",
        "설치시",
        "설치할",
        "설치하려",
        "설치하려고",
        "설치하기",
        "주의사항",
        "절차",
        "알려줘",
        "알려주세요",
        "어디에",
        "쓰는",
        "거야",
        "할거야",
        "예정",
        "예정이야",
        "해주세요",
        "하려고",
        "합니다",
        "할게",
        "내부",
        "내부를",
        "어떻게",
        "replacement",
        "installation",
    }
)
MAINTENANCE_ACTION_TERMS = frozenset(
    {
        "교체",
        "청소",
        "점검",
        "검사",
        "정비",
        "보수",
        "설치",
        "배선",
        "세척",
        "정렬",
        "조정",
        "해체",
        "분리",
        "연결",
        "운반",
        "차단",
        "격리",
        "잠금",
        "replace",
        "clean",
        "inspect",
        "check",
        "maintain",
        "install",
        "wire",
        "align",
        "adjust",
        "isolate",
        "lock",
    }
)
SAFETY_SIGNAL_TERMS = (
    "위험",
    "안전",
    "주의",
    "경고",
    "금지",
    "방호",
    "보호",
    "차단",
    "격리",
    "재가동",
    "인터락",
    "정지",
    "승인",
    "hazard",
    "warning",
    "caution",
    "lockout",
    "tagout",
    "interlock",
)
DOCUMENT_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("overview", ("개요", "소개", "목차", "표지", "overview", "introduction")),
    ("safety", ("안전", "주의", "경고", "위험", "금지", "safety", "warning", "caution")),
    ("procedure", ("설치", "점검", "검사", "정비", "보수", "배선", "설정", "교체", "절차", "install", "inspect", "maintenance", "wiring")),
    ("specification", ("사양", "규격", "모델", "구성", "정격", "치수", "spec", "model", "configuration")),
    ("troubleshooting", ("오류", "에러", "경고", "고장", "trouble", "error", "alarm")),
)


def normalize_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.strip().splitlines() if line.strip())


def psycopg_database_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def normalize_source_types(
    source_types: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if source_types is None:
        return None
    normalized: list[str] = []
    for source_type in source_types:
        value = source_type.strip()
        if not value:
            raise ValueError("source_types must not contain blank values")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("source_types must not be empty; use None for all sources")
    return tuple(normalized)


def normalize_document_ids(
    document_ids: Sequence[UUID] | None,
) -> tuple[UUID, ...] | None:
    if document_ids is None:
        return None
    normalized = tuple(dict.fromkeys(document_ids))
    if not normalized:
        raise ValueError("document_ids must not be empty; use None for all documents")
    return normalized


def scope_sql(
    source_types: tuple[str, ...] | None,
    document_ids: tuple[UUID, ...] | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if source_types is not None:
        clauses.append("AND d.source_type = ANY(%s)")
        parameters.append(list(source_types))
    if document_ids is not None:
        clauses.append("AND d.id = ANY(%s)")
        parameters.append(list(document_ids))
    return "\n".join(clauses), parameters


def tokenize(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            token.casefold()
            for token in re.findall(r"[0-9A-Za-z가-힣_-]+", value)
            if len(token) >= 2
        )
    )


def _term_matches(term: str, text: str) -> bool:
    if term in text:
        return True
    return len(term) >= 4 and any(
        len(candidate) >= 4 and (candidate in term or term in candidate)
        for candidate in tokenize(text)
    )


def lexical_score(query_terms: Sequence[str], text: str) -> float:
    if not query_terms:
        return 0.0
    normalized = text.casefold()
    matched = sum(1 for term in query_terms if _term_matches(term, normalized))
    return matched / len(query_terms)


def action_terms(value: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in tokenize(value):
        for action in MAINTENANCE_ACTION_TERMS:
            if token.startswith(action) or action in token:
                terms.append(action)
                break
    return tuple(dict.fromkeys(terms))


def safety_signal_score(text: str) -> float:
    if not text:
        return 0.0
    matched = sum(1 for term in SAFETY_SIGNAL_TERMS if term.casefold() in text)
    return min(1.0, matched / 3)


def topic_terms(value: str) -> tuple[str, ...]:
    return tuple(term for term in tokenize(value) if term not in GENERIC_QUERY_TERMS)


def _compact_for_phrase(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.casefold())


def _topic_phrase_terms(value: str) -> tuple[str, ...]:
    return topic_terms(value)[:2]


def _topic_phrase_matches(terms: Sequence[str], text: str) -> bool:
    if len(terms) < 2:
        return False
    compact_phrase = "".join(_compact_for_phrase(term) for term in terms)
    return bool(compact_phrase) and compact_phrase in _compact_for_phrase(text)


def _combined_row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            row["title"],
            row["section"],
            row["original_filename"],
            row["source_type"],
            row["content"],
        )
        if value
    )


def _document_category(row: dict[str, Any]) -> str:
    text = _combined_row_text(row).casefold()
    page_values = (row.get("page_start"), row.get("page"))
    if any(isinstance(page, int) and page <= 2 for page in page_values):
        return "overview"
    for category, keywords in DOCUMENT_CATEGORY_KEYWORDS:
        if any(keyword.casefold() in text for keyword in keywords):
            return category
    return "general"


def _diversify_document_ranked(
    ranked: list[tuple[float, dict[str, Any], float, float]],
) -> list[tuple[float, dict[str, Any], float, float]]:
    selected: list[tuple[float, dict[str, Any], float, float]] = []
    used_chunks: set[str] = set()
    for category, _ in DOCUMENT_CATEGORY_KEYWORDS:
        for item in ranked:
            row = item[1]
            chunk_id = str(row["chunk_id"])
            if chunk_id not in used_chunks and _document_category(row) == category:
                selected.append(item)
                used_chunks.add(chunk_id)
                break
    for item in ranked:
        chunk_id = str(item[1]["chunk_id"])
        if chunk_id not in used_chunks:
            selected.append(item)
            used_chunks.add(chunk_id)
    return selected


def topics_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    if not left or not right:
        return False
    left_text = " ".join(left)
    right_text = " ".join(right)
    return any(_term_matches(term, right_text) for term in left) or any(
        _term_matches(term, left_text) for term in right
    )


class BgeM3Embedder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._load_lock = Lock()
        self._encode_lock = Lock()

    def _get_model(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(
                        self.settings.model_name,
                        device=self.settings.device,
                        cache_folder=self.settings.model_cache_dir,
                    )
        return self._model

    def encode(self, text: str) -> np.ndarray:
        normalized = normalize_text(text)
        if not normalized:
            raise RetrievalError("The search query is empty.")
        with self._encode_lock:
            vector = self._get_model().encode(
                [normalized],
                batch_size=1,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0]
        return np.asarray(vector, dtype=np.float32)

    def encode_many(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        normalized = [normalize_text(text) for text in texts]
        if not normalized or any(not text for text in normalized):
            raise RetrievalError("Embedding input must contain non-empty text.")
        with self._encode_lock:
            vectors = self._get_model().encode(
                normalized,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return np.asarray(vectors, dtype=np.float32)


class PgvectorRetriever:
    def __init__(self, settings: Settings, embedder: BgeM3Embedder | None = None) -> None:
        self.settings = settings
        self.embedder = embedder or BgeM3Embedder(settings)

    def build_search_query(self, request: ChatRequest) -> str:
        analysis_keywords = request.analysis.search_keywords if request.analysis else []
        context_values: Sequence[str | None] = (
            request.context.equipment_name,
            request.context.manufacturer,
            request.context.model_number,
            request.context.component_name,
            request.context.task_type,
            request.context.energy_source,
            request.context.task_description,
        )
        context_text = " ".join(
            value.strip() for value in context_values if value and value.strip()
        )
        question_topics = topic_terms(request.question)
        context_topics = topic_terms(context_text)
        include_context = not question_topics or topics_overlap(
            question_topics, context_topics
        )
        relevant_analysis_keywords = [
            keyword
            for keyword in analysis_keywords
            if keyword.casefold() not in request.question.casefold()
            and (
                not question_topics
                or topics_overlap(question_topics, topic_terms(keyword))
            )
        ]
        occurrence_type = request.analysis.occurrence_type if request.analysis else None
        include_occurrence_type = bool(
            occurrence_type
            and (
                not question_topics
                or topics_overlap(question_topics, topic_terms(occurrence_type))
            )
        )
        values: list[str | None] = [request.question]
        if include_context:
            values.append(context_text)
        if request.analysis:
            values.extend(
                (
                    occurrence_type if include_occurrence_type else None,
                    request.analysis.work_type if include_context else None,
                    " ".join(relevant_analysis_keywords),
                )
            )
        return " ".join(value.strip() for value in values if value and value.strip())

    @staticmethod
    def _active_version_clause() -> str:
        return """
          (
              (d.current_version_id IS NULL AND dc.document_version_id IS NULL)
              OR (
                  dc.document_version_id = d.current_version_id
                  AND dv.status = 'active'
                  AND dv.is_active = true
              )
          )
        """

    def ready_chunk_count(
        self,
        source_types: Sequence[str] | None = None,
        document_ids: Sequence[UUID] | None = None,
    ) -> int:
        resolved_source_types = normalize_source_types(
            self.settings.source_types if source_types is None else source_types
        )
        resolved_document_ids = normalize_document_ids(document_ids)
        scope_clause, scope_parameters = scope_sql(
            resolved_source_types,
            resolved_document_ids,
        )
        query = f"""
            SELECT COUNT(*)
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            JOIN document_types dt ON dt.code = d.document_type_code
            LEFT JOIN document_versions dv ON dv.id = dc.document_version_id
            WHERE d.lifecycle_status = 'active'
              AND d.deleted_at IS NULL
              AND dt.is_active = true
              AND dt.scope = 'public'
              AND d.access_level = 'public'
              AND {self._active_version_clause()}
              {scope_clause}
              AND dc.embedding_status = 'ready'
              AND dc.embedding IS NOT NULL
              AND dc.embedding_model = %s
        """
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (*scope_parameters, self.settings.model_name))
                return int(cursor.fetchone()[0])

    def _candidate_query(
        self,
        scope_clause: str,
    ) -> str:
        return f"""
            WITH candidates AS (
                SELECT
                    d.id::text AS document_id,
                    dc.id::text AS chunk_id,
                    d.title,
                    d.document_type_code AS source_type,
                    dt.scope AS document_scope,
                    dv.id::text AS document_version_id,
                    dv.original_filename,
                    dv.version_number AS document_version,
                    COALESCE(dc.metadata->>'section', dc.section_path->>0) AS section,
                    dc.content,
                    dc.content_hash,
                    COALESCE(dc.page_number, dc.page_start) AS page,
                    dc.page_start,
                    dc.page_end,
                    d.publisher,
                    d.source_url AS url,
                    1 - (dc.embedding <=> %s) AS similarity,
                    ts_rank_cd(
                        to_tsvector('simple', COALESCE(dc.content, '')),
                        plainto_tsquery('simple', %s)
                    ) AS postgres_keyword_score
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                JOIN document_types dt ON dt.code = d.document_type_code
                LEFT JOIN document_versions dv ON dv.id = dc.document_version_id
                WHERE d.deleted_at IS NULL
                  AND dt.is_active = true
                  AND (
                      (
                          d.lifecycle_status = 'active'
                          AND {self._active_version_clause()}
                          AND (
                              (
                                  dt.scope = 'public'
                                  AND d.access_level = 'public'
                              )
                              OR (
                                  %s
                                  AND dt.scope = 'company'
                                  AND (%s OR d.access_level <> 'private')
                                  AND (%s OR d.site_id::text = ANY(%s))
                              )
                          )
                      )
                      OR (
                          NOT %s
                          AND d.id = ANY(%s::uuid[])
                          AND dt.scope = 'company'
                          AND dc.document_version_id = dv.id
                          AND dv.status = 'review_required'
                          AND dv.uploaded_by_user_id = %s::uuid
                          AND dv.version_number = (
                              SELECT MAX(owner_version.version_number)
                              FROM document_versions owner_version
                              WHERE owner_version.document_id = d.id
                                AND owner_version.status = 'review_required'
                                AND owner_version.uploaded_by_user_id = %s::uuid
                          )
                      )
                  )
                  AND (
                      %s
                      OR d.id = ANY(%s::uuid[])
                      OR (
                          %s
                          AND dt.scope = 'public'
                          AND d.access_level = 'public'
                      )
                  )
                  AND (
                      %s
                      OR dv.id = ANY(%s::uuid[])
                      OR (
                          %s
                          AND dt.scope = 'public'
                          AND d.access_level = 'public'
                      )
                  )
                  {scope_clause}
                  AND dc.embedding_status = 'ready'
                  AND dc.embedding IS NOT NULL
                  AND dc.embedding_model = %s
                  AND dc.embedding_dimension = %s
            )
            SELECT *
            FROM candidates
            WHERE similarity >= %s OR postgres_keyword_score > 0
            ORDER BY
                (similarity * 0.75 + LEAST(postgres_keyword_score, 1.0) * 0.25) DESC,
                chunk_id
            LIMIT %s
        """

    def search(
        self,
        request: InternalChatRequest,
        source_types: Sequence[str] | None = None,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ChatSource]:
        resolved_source_types = normalize_source_types(
            self.settings.source_types if source_types is None else source_types
        )
        resolved_document_ids = normalize_document_ids(document_ids)
        scope_clause, scope_parameters = scope_sql(
            resolved_source_types,
            resolved_document_ids,
        )
        search_query = self.build_search_query(request)
        vector = self.embedder.encode(search_query)
        selected_documents = request.context.effective_document_ids()
        selected_versions = tuple(request.context.selected_document_version_ids)
        include_all_documents = not selected_documents
        include_all_versions = not selected_versions
        include_public_supplement = self._include_public_supplement(
            request,
            document_ids,
        )
        parameters = (
            vector,
            search_query,
            request.access_scope.allow_company,
            request.access_scope.allow_private,
            request.access_scope.all_sites,
            request.access_scope.site_ids,
            include_all_documents,
            list(selected_documents),
            request.access_scope.requester_user_id,
            request.access_scope.requester_user_id,
            include_all_documents,
            list(selected_documents),
            include_public_supplement,
            include_all_versions,
            list(selected_versions),
            include_public_supplement,
            *scope_parameters,
            self.settings.model_name,
            int(vector.shape[0]),
            self.settings.min_similarity,
            max(self.settings.candidate_k, self.settings.top_k),
        )
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
            row_factory=dict_row,
        ) as connection:
            register_vector(connection)
            with connection.cursor() as cursor:
                cursor.execute(self._candidate_query(scope_clause), parameters)
                rows = cursor.fetchall()
        return self._rerank(request, rows)

    @staticmethod
    def _include_public_supplement(
        request: InternalChatRequest,
        document_ids: tuple[UUID, ...] | None,
    ) -> bool:
        intent = request.analysis.question_intent if request.analysis else None
        return bool(
            request.context.effective_document_ids()
            and request.access_scope.allow_company
            and document_ids is None
            and intent == "maintenance_guide"
        )

    def _rerank(
        self,
        request: InternalChatRequest,
        rows: Sequence[dict[str, Any]],
    ) -> list[ChatSource]:
        query = self.build_search_query(request)
        query_terms = tokenize(query)
        query_action_terms = action_terms(request.question)
        context_topic_values = [
            request.context.equipment_name,
            request.context.manufacturer,
            request.context.model_number,
            request.context.component_name,
        ]
        if request.analysis:
            context_topic_values.extend(request.analysis.equipment)
            context_topic_values.extend(request.analysis.component)
        context_topics = topic_terms(
            " ".join(value for value in context_topic_values if value)
        )
        question_topics = topic_terms(request.question)
        active_topic_terms = question_topics or context_topics
        if not active_topic_terms:
            active_topic_terms = tuple(
                term for term in query_terms if term not in GENERIC_QUERY_TERMS
            )
        intent = request.analysis.question_intent if request.analysis else None
        selected_document_ids = {
            str(document_id) for document_id in request.context.effective_document_ids()
        }
        selected_topic_phrase_terms = _topic_phrase_terms(request.question)
        selected_documents_match_topic_phrase = bool(
            selected_document_ids
            and selected_topic_phrase_terms
            and any(
                str(row["document_id"]) in selected_document_ids
                and _topic_phrase_matches(selected_topic_phrase_terms, _combined_row_text(row))
                for row in rows
            )
        )

        ranked: list[tuple[float, dict[str, Any], float, float]] = []
        seen_hashes: set[str] = set()
        for row in rows:
            content = str(row["content"])
            metadata_text = " ".join(
                str(value)
                for value in (
                    row["title"],
                    row["section"],
                    row["original_filename"],
                    row["source_type"],
                )
                if value
            )
            combined = f"{metadata_text} {content}".casefold()
            row_document_id = str(row["document_id"])
            document_scope = str(row["document_scope"] or "").casefold()
            source_type = str(row["source_type"] or "").casefold()
            if (
                intent == "maintenance_guide"
                and selected_documents_match_topic_phrase
                and row_document_id not in selected_document_ids
                and document_scope == "public"
                and not _topic_phrase_matches(selected_topic_phrase_terms, combined)
            ):
                continue
            has_topic_match = (
                not active_topic_terms
                or any(_term_matches(term, combined) for term in active_topic_terms)
            )
            if active_topic_terms and not has_topic_match:
                continue
            keyword = lexical_score(query_terms, combined)
            similarity = float(row["similarity"])
            if (
                similarity < self.settings.min_similarity
                and keyword < self.settings.min_keyword_score
            ):
                continue
            content_hash = str(row["content_hash"])
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            metadata_score = lexical_score(
                active_topic_terms, metadata_text.casefold()
            )
            action_score = lexical_score(query_action_terms, combined)
            safety_score = safety_signal_score(combined)
            retrieval_score = max(0.0, similarity) * 0.7 + keyword * 0.3
            reranker_score = retrieval_score * 0.9 + metadata_score * 0.1
            if intent == "maintenance_guide":
                if source_type in {
                    "manual",
                    "equipment_manual",
                    "component_manual",
                    "work_standard",
                }:
                    reranker_score += 0.08
                elif source_type in {"public_guide", "public_incident", "regulation"}:
                    reranker_score += 0.03
                reranker_score += action_score * 0.12 + safety_score * 0.04
                if query_action_terms and action_score == 0.0 and document_scope == "public":
                    reranker_score = max(0.0, reranker_score - 0.06)
            elif intent == "component_info":
                if source_type in {
                    "manual",
                    "equipment_manual",
                    "component_manual",
                    "public_guide",
                }:
                    reranker_score += 0.05
                elif source_type in {"public_incident", "incident"}:
                    reranker_score = max(0.0, reranker_score - 0.03)
            ranked.append((reranker_score, row, keyword, retrieval_score))

        ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        if intent == "document_qa":
            ranked = _diversify_document_ranked(ranked)
        per_document: dict[str, int] = {}
        sources: list[ChatSource] = []
        max_chunks_per_document = self.settings.max_chunks_per_document
        if intent == "document_qa":
            max_chunks_per_document = max(max_chunks_per_document, min(4, self.settings.top_k))
        for reranker_score, row, keyword, retrieval_score in ranked:
            document_id = row["document_id"]
            if per_document.get(document_id, 0) >= max_chunks_per_document:
                continue
            per_document[document_id] = per_document.get(document_id, 0) + 1
            sources.append(
                ChatSource(
                    document_id=document_id,
                    document_version_id=row.get("document_version_id"),
                    chunk_id=row["chunk_id"],
                    title=row["title"],
                    source_type=row["source_type"],
                    document_scope=row["document_scope"],
                    original_filename=row["original_filename"],
                    document_version=row["document_version"],
                    section=row["section"],
                    excerpt=str(row["content"])[:700],
                    page=row["page"],
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    publisher=row["publisher"],
                    url=row["url"],
                    similarity=float(row["similarity"]),
                    keyword_score=keyword,
                    retrieval_score=max(0.0, retrieval_score),
                    reranker_score=max(0.0, reranker_score),
                )
            )
            if len(sources) >= self.settings.top_k:
                break
        return sources

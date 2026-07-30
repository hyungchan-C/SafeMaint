from ai.embeddings import ingest_pdf


def test_ingest_pdf_loads_pending_chunks_without_embedding(monkeypatch) -> None:
    fake_document = {"external_id": "manual:테스트:M1:sample", "metadata": {}}
    fake_chunks = [
        {
            "chunk_index": 0,
            "content": "첫 번째 청크",
            "content_hash": "h0",
            "embedding_status": "pending",
        },
        {
            "chunk_index": 1,
            "content": "두 번째 청크",
            "content_hash": "h1",
            "embedding_status": "pending",
        },
    ]

    monkeypatch.setattr(
        ingest_pdf,
        "process_pdf",
        lambda *a, **k: {"document": fake_document, "chunks": fake_chunks},
    )

    captured: dict = {}

    def fake_load_document(document, chunks):
        captured["document"] = document
        captured["chunks"] = chunks
        return "fake-uuid"

    monkeypatch.setattr(ingest_pdf, "load_document", fake_load_document)

    result = ingest_pdf.ingest_pdf(
        "sample.pdf", product_type="센서", model_name="M1"
    )

    assert result == {"document_id": "fake-uuid", "chunk_count": 2}
    assert captured["document"] is fake_document
    # 임베딩은 여기서 만들지 않는다 - embed_pending_chunks()가 나중에 배치로 처리
    assert captured["chunks"] == fake_chunks
    assert all("embedding" not in chunk for chunk in captured["chunks"])
    assert all(chunk["embedding_status"] == "pending" for chunk in captured["chunks"])

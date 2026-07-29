import fitz

from app.services.pdf_outline import (
    OutlineChapter,
    chapter_title_for_page,
    document_outline_chapters,
)


def _write_pdf_with_toc(path: str, *, page_count: int, toc: list[tuple[int, str, int]]) -> None:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    document.set_toc(toc)
    document.save(path)
    document.close()


def test_document_outline_chapters_computes_page_ranges_between_top_level_entries(
    tmp_path,
) -> None:
    pdf_path = str(tmp_path / "manual.pdf")
    _write_pdf_with_toc(
        pdf_path,
        page_count=25,
        toc=[
            (1, "1. 개요", 1),
            (2, "1.1 특징", 1),
            (1, "2. 정격 및 성능", 16),
            (2, "2.1 일반형", 16),
            (2, "2.3 공통", 17),
            (1, "3. 외형치수도", 23),
        ],
    )

    chapters = document_outline_chapters(pdf_path)

    assert [c.title for c in chapters] == ["1. 개요", "2. 정격 및 성능", "3. 외형치수도"]
    ratings_chapter = chapters[1]
    assert ratings_chapter.start_page == 16
    assert ratings_chapter.end_page == 22


def test_document_outline_chapters_returns_empty_for_missing_file() -> None:
    assert document_outline_chapters("/no/such/file.pdf") == []


def test_document_outline_chapters_returns_empty_when_pdf_has_no_bookmarks(
    tmp_path,
) -> None:
    pdf_path = str(tmp_path / "no_toc.pdf")
    _write_pdf_with_toc(pdf_path, page_count=3, toc=[])

    assert document_outline_chapters(pdf_path) == []


def test_chapter_title_for_page_matches_the_containing_range() -> None:
    chapters = [
        OutlineChapter("1. 개요", 1, 15),
        OutlineChapter("2. 정격 및 성능", 16, 22),
        OutlineChapter("3. 외형치수도", 23, 10**9),
    ]

    assert chapter_title_for_page(chapters, 16) == "2. 정격 및 성능"
    assert chapter_title_for_page(chapters, 20) == "2. 정격 및 성능"
    assert chapter_title_for_page(chapters, 22) == "2. 정격 및 성능"
    assert chapter_title_for_page(chapters, 15) == "1. 개요"
    assert chapter_title_for_page(chapters, 23) == "3. 외형치수도"
    assert chapter_title_for_page(chapters, None) is None
    assert chapter_title_for_page([], 16) is None

from __future__ import annotations

from typing import NamedTuple

import fitz


class OutlineChapter(NamedTuple):
    title: str
    start_page: int
    end_page: int


def document_outline_chapters(storage_path: str) -> list[OutlineChapter]:
    """Top-level bookmark chapters of a PDF, in page order.

    Some PDFs (e.g. manuals with a proper table of contents) carry the real
    chapter title — "2. 정격 및 성능" — as an embedded bookmark even though the
    per-chunk section metadata only kept the leaf subsection heading beneath
    it ("2.1 일반형"). This reads that bookmark tree directly from the file, so
    a page can be matched against its real chapter title without guessing or
    inferring one. Returns [] if the file is missing, unreadable, or has no
    bookmarks — callers then simply see no extra signal and fall back to
    whatever section metadata already exists, unchanged.
    """

    try:
        with fitz.open(storage_path) as document:
            toc = document.get_toc()
    except Exception:
        return []

    top_level = [
        (cleaned, page)
        for level, title, page in toc
        # A leading BOM (U+FEFF) shows up in some PDFs' bookmark titles as a
        # text-extraction artifact — it's invisible but would otherwise leak
        # into the section text shown in the UI.
        if level == 1 and page > 0 and (cleaned := title.replace("﻿", "").strip())
    ]
    chapters: list[OutlineChapter] = []
    for index, (title, start_page) in enumerate(top_level):
        end_page = (
            top_level[index + 1][1] - 1
            if index + 1 < len(top_level)
            else 10**9
        )
        chapters.append(OutlineChapter(title, start_page, max(start_page, end_page)))
    return chapters


def chapter_title_for_page(
    chapters: list[OutlineChapter], page: int | None
) -> str | None:
    if page is None:
        return None
    for chapter in chapters:
        if chapter.start_page <= page <= chapter.end_page:
            return chapter.title
    return None

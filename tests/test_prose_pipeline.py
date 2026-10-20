"""Integration boundaries for historical layout refresh and source prose extraction."""
import copy
from types import SimpleNamespace

import pymupdf
import pytest

from packages.common.config import Settings
from packages.document_processing.chunks import chunk_pages
from packages.document_processing.prose import VERSION, paragraph_spans
from packages.providers.bibliography import SourceResolver, page, plain_html
from services.knowledge_service.pipeline import refreshed_layout


def marked_page(paragraphs, separator="\n\n"):
    text = separator.join(paragraphs)
    spans, start = [], 0
    for paragraph in paragraphs:
        spans.append({"offset_start": start, "offset_end": start + len(paragraph)})
        start += len(paragraph) + len(separator)
    return {
        "normalized_text": text, "raw_text": text, "blocks": [{"origin": "fresh", "offset_start": 0, "offset_end": len(text)}],
        "quality": {"prose_filter_version": VERSION, "prose_paragraphs": spans},
    }


def selected_text(page_record):
    return [page_record["normalized_text"][span["offset_start"]:span["offset_end"]]
            for span in paragraph_spans(page_record)]


def test_identical_text_refresh_replaces_only_layout_and_preserves_previous_citation_offsets():
    text = "Silva (2020) describes the original finding."
    previous = {"normalized_text": text, "raw_text": "RAW PRESERVED", "quality": 1.0,
                "blocks": [{"origin": "old"}], "id": "old-page"}
    fresh = marked_page([text])
    original = copy.deepcopy(previous)
    citation_start = text.index("Silva")
    updates = refreshed_layout(previous, fresh)
    assert previous == original
    assert set(updates) == {"quality", "blocks"}
    refreshed = {**previous, **updates}
    assert refreshed["raw_text"] == "RAW PRESERVED"
    assert refreshed["normalized_text"][citation_start:citation_start + 5] == "Silva"
    assert refreshed["quality"] == fresh["quality"]
    assert refreshed["blocks"] == fresh["blocks"]
    assert selected_text(refreshed) == [text]


def test_whitespace_remap_uses_exact_historical_substrings_and_keeps_original_blocks():
    first = "Silva (2020) describes the original finding."
    second = "Costa (2021) describes another complete observation."
    original_text = "  Silva  (2020)\ndescribes the original finding.\n\n\nCosta (2021)\tdescribes another complete observation.  "
    previous = {"normalized_text": original_text, "raw_text": "RAW", "blocks": [{"origin": "old"}]}
    fresh = marked_page([first, second])
    fresh["quality"]["prose_excluded_spans"] = [{"offset_start": 2, "offset_end": 5, "kind": "table"}]
    original_fresh = copy.deepcopy(fresh)
    updates = refreshed_layout(previous, fresh)
    refreshed = {**previous, **updates}
    assert selected_text(refreshed) == [
        "Silva  (2020)\ndescribes the original finding.",
        "Costa (2021)\tdescribes another complete observation.",
    ]
    assert refreshed["blocks"] is previous["blocks"]
    assert "prose_layout_note" in refreshed["quality"]
    assert "prose_excluded_spans" not in refreshed["quality"]
    assert fresh == original_fresh
    assert refreshed["raw_text"] == "RAW"


def test_ambiguous_and_changed_ocr_paragraphs_are_omitted_but_unique_following_prose_survives():
    duplicate = "Silva (2020) reports the same paragraph."
    changed = "Costa (2021) observes a 10 percent improvement."
    unique = "Pereira (2022) describes an independent finding."
    previous = {"normalized_text": duplicate + "\n\n" + duplicate + "\n\n"
                + changed.replace("10 percent", "70 percent") + "\n\n" + unique,
                "blocks": [{"origin": "historical"}]}
    fresh = marked_page([duplicate, changed, unique])
    refreshed = {**previous, **refreshed_layout(previous, fresh)}
    assert selected_text(refreshed) == [unique]
    assert "sem correspondência única foram excluídos" in refreshed["quality"]["prose_layout_note"]


@pytest.mark.parametrize("historical", [
    "DeSilva (2020) describes the finding.",
    "Silva (2020) describes the finding.extra",
])
def test_layout_remap_never_matches_inside_a_changed_word(historical):
    previous = {"normalized_text": historical, "blocks": []}
    fresh = marked_page(["Silva (2020) describes the finding."])
    refreshed = {**previous, **refreshed_layout(previous, fresh)}
    assert paragraph_spans(refreshed) == []


def test_new_authoritative_empty_layout_never_reenables_old_non_prose():
    previous = {"normalized_text": "Silva (2020) is only a diagram label.", "blocks": []}
    fresh = marked_page([])
    refreshed = {**previous, **refreshed_layout(previous, fresh)}
    assert paragraph_spans(refreshed) == []
    assert refreshed["normalized_text"] == previous["normalized_text"]


def test_html_source_pipeline_excludes_non_prose_elements_and_keeps_paragraph_offsets():
    html = """<html><body>
    <header><p>HEADER Silva (1990)</p></header>
    <h1>HEADING Silva (1991)</h1>
    <p>Silva (2020) describes <em>an improvement</em> in attention.<br>
    This sentence remains inside the first paragraph.</p>
    <table><tr><td>TABLE Costa (1992)</td><td>10</td></tr></table>
    <figure><p>FIGURE Costa (1993)</p><figcaption>CAPTION Costa (1994)</figcaption></figure>
    <svg><text>DIAGRAM Costa (1995)</text></svg>
    <canvas>CANVAS Costa (1996)</canvas>
    <script>window.secret = 'SCRIPT';</script>
    <ul><li>LIST item outside running prose</li></ul>
    <p>Costa (2021) reports limitations &amp; uncertainty.</p>
    <footer>FOOTER Silva (1997)</footer>
    </body></html>"""
    text = plain_html(html)
    record = page(text)
    paragraphs = selected_text(record)
    assert len(paragraphs) == 2
    assert "an improvement" in paragraphs[0] and "first paragraph" in paragraphs[0]
    assert paragraphs[1] == "Costa (2021) reports limitations & uncertainty."
    assert not any(label in text for label in ("HEADER", "HEADING", "TABLE", "FIGURE", "CAPTION", "DIAGRAM", "CANVAS", "SCRIPT", "LIST", "FOOTER"))
    chunks = chunk_pages([record], origin="SOURCE", document_id="document", document_version_id="version",
                         user_id="user", source_id="source", work_id="work")["chunks"]
    assert [chunk["text"] for chunk in chunks] == paragraphs
    assert all(text[chunk["offset_start"]:chunk["offset_end"]] == chunk["text"] for chunk in chunks)


def test_jats_namespace_paragraphs_and_nested_exclusions_remain_separate():
    html = ("<jats:p>Silva (2020) describes observations.</jats:p>"
            "<figure><svg><text>IGNORED</text></svg><p>ALSO IGNORED</p></figure>"
            "<jats:p>Costa (2021) reports an independent result.</jats:p>")
    assert selected_text(page(plain_html(html))) == [
        "Silva (2020) describes observations.",
        "Costa (2021) reports an independent result.",
    ]


def test_source_pdf_extraction_retains_geometry_and_excludes_table_before_chunking():
    title = "Effects of breaks on attention"
    with pymupdf.open() as document:
        pdf_page = document.new_page()
        pdf_page.insert_text((50, 55), title, fontsize=16, fontname="hebo")
        pdf_page.insert_text((50, 80), "Ana Silva", fontsize=11)
        pdf_page.insert_text((50, 120), "Silva (2020) describes improved attention in forty adults.", fontsize=11)
        pdf_page.insert_text((50, 135), "The design is observational and cannot demonstrate causality.", fontsize=11)
        pdf_page.insert_text((50, 180), "Table 1: Measures by group", fontsize=9)
        for y in (200, 225, 250):
            pdf_page.draw_line((50, y), (400, y))
        for x in (50, 250, 400):
            pdf_page.draw_line((x, 200), (x, 250))
        pdf_page.insert_text((60, 216), "TABLE MARKER Silva (1990)", fontsize=10)
        pdf_page.insert_text((260, 216), "10", fontsize=10)
        pdf_page.insert_text((60, 241), "OTHER TABLE DATA", fontsize=10)
        pdf_page.insert_text((260, 241), "20", fontsize=10)
        pdf_page.insert_text((50, 300), "Costa (2021) discusses limitations of the selected sample.", fontsize=11)
        data = document.tobytes()
    resolver = SourceResolver(Settings(_env_file=None))
    pages, access, limitations = resolver._extract(
        SimpleNamespace(data=data, content_type="application/pdf"),
        {"title": title, "authors": [{"family": "Silva"}]},
    )
    assert access == "PARTIAL_TEXT" and limitations
    assert len(pages) == 1
    source_page = pages[0]
    assert source_page["quality"]["prose_filter_version"] == VERSION
    assert source_page["blocks"] and source_page["blocks"][0]["bbox"]
    assert "TABLE MARKER" in source_page["normalized_text"]
    paragraphs = selected_text(source_page)
    assert len(paragraphs) == 2
    assert "causality" in paragraphs[0]
    assert "TABLE MARKER" not in "\n".join(paragraphs)
    chunks = chunk_pages(pages, origin="SOURCE", document_id="document", document_version_id="version",
                         user_id="user", source_id="source", work_id="work")["chunks"]
    assert [chunk["text"] for chunk in chunks] == paragraphs

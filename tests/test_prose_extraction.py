"""Prose paragraphs must exclude visible non-prose while keeping exact PDF offsets."""
import io

import pymupdf
import pytest
from PIL import Image, ImageDraw

from packages.document_processing.pdf import _ocr, extract_pdf
from packages.document_processing.prose import VERSION, analyze_prose, paragraph_spans, raster_grid_regions


def snippets(page):
    text = page["normalized_text"]
    return [text[span["offset_start"]:span["offset_end"]] for span in paragraph_spans(page)]


def mixed_pdf():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 45), "1. Introduction", fontsize=16, fontname="hebo")
        page.insert_text((50, 80), "Silva (2020) describes an improvement in attention.", fontsize=11)
        page.insert_text((50, 95), "This second sentence belongs to the same paragraph.", fontsize=11)
        page.insert_text((50, 135), "Costa (2021) identifies limitations in the observed sample.", fontsize=11)
        page.insert_text((50, 190), "Table 1: Experimental measurements", fontsize=9)
        for y in (210, 235, 260, 285):
            page.draw_line((50, y), (400, y))
        for x in (50, 210, 310, 400):
            page.draw_line((x, 210), (x, 285))
        for row, cells in enumerate([
            ["Author", "Group", "Outcome"], ["Silva (2020)", "15", "0.8"], ["Costa (2021)", "20", "0.6"],
        ]):
            for x, value in zip((57, 217, 317), cells):
                page.insert_text((x, 226 + row * 25), value, fontsize=10)
        page.draw_rect(pymupdf.Rect(70, 340, 200, 390))
        page.draw_rect(pymupdf.Rect(290, 340, 440, 390))
        page.draw_line((200, 365), (290, 365))
        page.insert_text((80, 368), "Input Silva (2020)", fontsize=10)
        page.insert_text((300, 368), "Output Costa (2021)", fontsize=10)
        page.insert_text((50, 420), "Figure 1: Diagram labels must not become claims.", fontsize=9)
        # A small raster figure with an OCR/native overlay must also be excluded.
        image = Image.new("RGB", (300, 100), "lightgray")
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        page.insert_image(pymupdf.Rect(50, 460, 450, 540), stream=image_bytes.getvalue())
        page.insert_text((70, 500), "Image label Silva (2020) should not be prose.", fontsize=11)
        page.insert_text((50, 590), "The final paragraph discusses the practical implications.", fontsize=11)
        page.insert_text((295, 810), "12", fontsize=10)
        return document.tobytes()


def test_native_pdf_keeps_paragraphs_and_excludes_table_diagram_image_captions():
    page = extract_pdf(mixed_pdf(), ocr_enabled=False)["pages"][0]
    paragraphs = snippets(page)
    assert len(paragraphs) == 3
    assert "same paragraph" in paragraphs[0] and "Silva (2020)" in paragraphs[0]
    assert paragraphs[1].startswith("Costa (2021)")
    assert paragraphs[2].startswith("The final paragraph")
    assert not any(word in "\n".join(paragraphs) for word in ("Experimental measurements", "Outcome", "Input Silva", "Image label", "Diagram labels"))
    # Full extraction is preserved for bibliography, provenance and review.
    assert "Input Silva (2020)" in page["normalized_text"]
    assert "Outcome" in page["raw_text"]
    assert page["quality"]["prose_filter_version"] == VERSION
    assert set(page["quality"]["prose_excluded_counts"]) >= {"heading", "folio", "caption", "table", "image"}
    assert any(region["kind"] == "diagram" for region in page["quality"]["prose_regions"])
    assert all(0 <= span["offset_start"] < span["offset_end"] <= len(page["normalized_text"])
               for span in paragraph_spans(page))


def test_unruled_aligned_table_is_excluded_but_body_columns_are_preserved():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "An ordinary paragraph discusses published evidence.", fontsize=11)
        for row, cells in enumerate([
            ["Author", "Group", "Score"], ["Silva (2020)", "15", "0.8"], ["Costa (2021)", "20", "0.6"],
        ]):
            for x, value in zip((50, 240, 400), cells):
                page.insert_text((x, 140 + row * 25), value, fontsize=10)
        page.insert_text((50, 250), "The left column has a complete sentence.", fontsize=10)
        page.insert_text((320, 250), "The right column contains another sentence.", fontsize=10)
        page.insert_text((50, 270), "These results require further observation.", fontsize=10)
        page.insert_text((320, 270), "These limitations affect interpretation.", fontsize=10)
        result = extract_pdf(document.tobytes(), ocr_enabled=False)["pages"][0]
    text = "\n".join(snippets(result))
    assert "ordinary paragraph" in text
    assert "Author" not in text and "Silva (2020)" not in text
    assert "left column" in text and "right column" in text
    assert result["quality"]["prose_excluded_counts"]["table"] >= 6


def test_indentation_splits_paragraphs_without_blank_lines():
    text = "First paragraph has a complete statement.\nSecond paragraph is indented and independent."
    split = text.index("\n")
    page = {"normalized_text": text, "blocks": [
        {"offset_start": 0, "offset_end": split, "bbox": [50, 50, 300, 64], "font_size": 10},
        {"offset_start": split + 1, "offset_end": len(text), "bbox": [65, 66, 360, 80], "font_size": 10},
    ]}
    assert len(paragraph_spans(page)) == 2


def test_scanned_page_keeps_ocr_paragraphs_and_uses_raster_table_regions(monkeypatch):
    image = Image.new("RGB", (600, 840), "white")
    draw = ImageDraw.Draw(image)
    for y in (220, 250, 280, 310):
        draw.line((50, y, 500, y), fill="black", width=2)
    for x in (50, 250, 500):
        draw.line((x, 220, x, 310), fill="black", width=2)
    regions = raster_grid_regions(image, 1)
    assert regions and regions[0]["kind"] == "raster_grid"
    lines = [
        ("Silva (2020) describes an improvement in attention.", [50, 60, 500, 75], [1, 1]),
        ("The next sentence continues the original paragraph.", [50, 80, 500, 95], [1, 1]),
        ("Costa (2021) describes a separate limitation.", [50, 110, 480, 125], [1, 2]),
        ("Silva (2020) table data are not manuscript prose.", [60, 230, 480, 245], [2, 1]),
        ("A final paragraph remains after the scanned table.", [50, 350, 500, 365], [3, 1]),
    ]
    text, blocks, offset = [], [], 0
    for value, box, paragraph in lines:
        blocks.append({"text": value, "bbox": box, "offset_start": offset,
                       "offset_end": offset + len(value), "paragraph_id": paragraph, "font_size": 11})
        text.append(value)
        offset += len(value) + 1
    blocks[0]["raster_exclusions"] = regions
    monkeypatch.setattr("packages.document_processing.pdf._ocr", lambda *args: ("\n".join(text), blocks, .94))
    output = io.BytesIO()
    image.save(output, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=840)
        page.insert_image(page.rect, stream=output.getvalue())
        extracted = extract_pdf(document.tobytes())["pages"][0]
    assert extracted["extraction_method"] == "OCR"
    paragraphs = snippets(extracted)
    assert len(paragraphs) == 3
    assert "next sentence" in paragraphs[0]
    assert not any("table data" in value for value in paragraphs)
    assert extracted["quality"]["prose_excluded_counts"]["raster_grid"] == 1


def test_actual_ocr_adapter_records_paragraph_ids_words_and_unchanged_line_offsets(monkeypatch):
    import pytesseract

    values = {
        "text": ["First", "paragraph", "continues.", "Second", "paragraph."],
        "block_num": [1] * 5, "par_num": [1, 1, 1, 2, 2], "line_num": [1, 1, 2, 1, 1],
        "left": [50, 100, 50, 50, 120], "top": [50, 50, 80, 120, 120],
        "width": [40, 80, 100, 60, 90], "height": [15] * 5, "conf": [95] * 5,
    }
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *args, **kwargs: values)
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=250)
        text, blocks, confidence = _ocr(page, "eng", 5, 50_000)
    assert text == "First paragraph\ncontinues.\nSecond paragraph."
    assert blocks[0]["paragraph_id"] == blocks[1]["paragraph_id"] == [1, 1]
    assert blocks[2]["paragraph_id"] == [1, 2]
    assert blocks[0]["words"][0]["text"] == "First"
    assert confidence == pytest.approx(.95)
    assert all(text[block["offset_start"]:block["offset_end"]] == block["text"] for block in blocks)


def test_legacy_fallback_retains_wrapped_prose_and_rejects_explicit_non_prose():
    text = ("Introduction\n\nSilva (2020) describes a result\nwith carefully controlled observations.\n\n"
            "Table 1: Results\nAuthor | Score | Group\nSilva | 10 | A\n\n"
            "Costa (2021) describes limitations.\n\n12")
    page = {"normalized_text": text}
    assert snippets(page) == [
        "Silva (2020) describes a result\nwith carefully controlled observations.",
        "Costa (2021) describes limitations.",
    ]


def test_wrapped_caption_is_not_reintroduced_as_a_prose_paragraph():
    text = ("Figure 1: Initial caption description\n"
            "Silva (2020) is cited inside the continuation of this caption.\n\n"
            "Costa (2021) describes a separate body paragraph.")
    assert snippets({"normalized_text": text}) == ["Costa (2021) describes a separate body paragraph."]


def test_authoritative_empty_and_invalid_metadata_never_fall_back_to_unfiltered_text():
    page = {"normalized_text": "Silva (2020) is a label inside a diagram.", "quality": {
        "prose_filter_version": VERSION, "prose_paragraphs": [],
    }}
    assert paragraph_spans(page) == []
    page["quality"]["prose_paragraphs"] = [{"offset_start": 0, "offset_end": 5000}]
    with pytest.raises(ValueError, match="Intervalo"):
        paragraph_spans(page)


def test_legacy_html_block_with_no_geometry_keeps_separate_paragraphs_and_numeric_quality():
    from packages.providers.bibliography import DEMO_TEXT, page

    first = "Silva (2020) apresenta resultados de uma pesquisa."
    second = "Costa (2021) relata limitações para a interpretação."
    legacy = page(first + "\n\n" + second)
    legacy["quality"] = 1.0
    assert snippets(legacy) == [first, second]
    assert snippets(page(DEMO_TEXT)) == [DEMO_TEXT]


def test_layout_detection_failure_is_disclosed_without_destroying_original_text(monkeypatch):
    monkeypatch.setattr(pymupdf.Page, "find_tables", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic")))
    result = extract_pdf(mixed_pdf(), ocr_enabled=False)
    page = result["pages"][0]
    assert page["normalized_text"] and page["quality"]["prose_filter_warnings"]
    assert any("tabelas indisponível" in message for message in result["warnings"])

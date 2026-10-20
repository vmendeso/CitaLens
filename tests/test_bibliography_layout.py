"""Synthetic regressions for the multi-column bibliography seen in a live audit."""
from packages.document_processing.bibliography import catalog_references, map_citations


def layout_page(number, blocks):
    text = ""
    geometry = []
    for bbox, content in blocks:
        if text:
            text += "\n\n"
        start = len(text)
        text += content
        geometry.append({"bbox": bbox, "offset_start": start, "offset_end": len(text),
                         "text": content, "is_folio": False})
    return {"physical_page_number": number, "normalized_text": text, "raw_text": text,
            "blocks": geometry, "quality": {}, "status": "EXTRACTED"}


def test_two_columns_preserve_entry_continuations_and_original_offsets():
    page = layout_page(1, [
        ([40, 40, 540, 55], "References"),
        ([40, 80, 270, 95], "[1] J. Silva, A. Costa, First synthetic"),
        ([310, 80, 540, 95], "[3] M. Chen, Third synthetic"),
        ([40, 100, 270, 115], "article title, Example Journal 4 (2020) 1-8."),
        ([310, 100, 540, 115], "article title, Example Journal 5 (2022) 8-12."),
        ([40, 140, 270, 165], "[2] B. Lima, Second synthetic article, 2021."),
        ([310, 140, 540, 165], "[4] C. Zhou, Fourth synthetic article, 2023."),
    ])
    original = page["normalized_text"]
    catalog = catalog_references([page])
    refs = catalog["references"]
    assert [ref["entry_number"] for ref in refs] == [1, 2, 3, 4]
    assert [ref["title"] for ref in refs] == [
        "First synthetic article title", "Second synthetic article",
        "Third synthetic article title", "Fourth synthetic article"]
    assert refs[0]["authors"] == ["J. Silva", "A. Costa"]
    assert all(not ref["issues"] for ref in refs)
    assert page["normalized_text"] == original
    assert catalog["bibliography_regions"] == [{"page_number": 1, "start": 0, "end": len(original)}]


def test_bibliography_in_right_column_does_not_swallow_left_column_body():
    page = layout_page(1, [
        ([40, 40, 270, 65], "O primeiro parágrafo apresenta os resultados do estudo."),
        ([310, 40, 540, 65], "O último parágrafo descreve a conclusão do estudo."),
        ([310, 90, 390, 105], "References"),
        ([310, 130, 540, 155], "[1] J. Silva, Synthetic article title, 2020."),
        ([40, 180, 270, 205], "Outra passagem do corpo ainda discute os resultados."),
        ([40, 240, 270, 265], "Uma observação no corpo cita o estudo [1]."),
        ([310, 240, 540, 265], "[2] A. Costa, A different synthetic title, 2021."),
    ])
    catalog = catalog_references([page])
    assert len(catalog["references"]) == 2
    assert all("passagem" not in ref["raw_text"] for ref in catalog["references"])
    body_position = page["normalized_text"].index("Uma observação")
    assert not any(region["start"] <= body_position < region["end"]
                   for region in catalog["bibliography_regions"])
    # Supply authoritative prose intervals so this test isolates catalog masking.
    block = page["blocks"][5]
    page["quality"] = {"prose_filter_version": "paragraphs-v1", "prose_paragraphs": [
        {"offset_start": block["offset_start"], "offset_end": block["offset_end"]}]}
    mapped = map_citations([page], catalog)
    assert len(mapped["occurrences"]) == 1
    assert mapped["occurrences"][0]["binding_status"] == "RESOLVED"


def test_numbered_author_continuation_is_not_an_extra_reference():
    page = layout_page(1, [([40, 80, 540, 180],
        "References\n[1] A. Silva, B. Costa,\nZhang, A., Synthetic entry title, 2020.\n"
        "[2] C. Lima, Another synthetic title, 2021.")])
    refs = catalog_references([page])["references"]
    assert len(refs) == 2
    assert "Zhang, A." in refs[0]["raw_text"]


def test_repeated_running_header_does_not_contaminate_cross_page_entry():
    pages = [layout_page(1, [
        ([40, 35, 540, 45], "Synthetic Authors / Journal of Examples"),
        ([40, 100, 540, 115], "References"),
        ([40, 150, 540, 165], "[1] A. Silva, A title continuing"),
    ]), layout_page(2, [
        ([40, 35, 540, 45], "Synthetic Authors / Journal of Examples"),
        ([40, 100, 540, 115], "across pages, Example Journal 2 (2020) 1-9."),
        ([40, 150, 540, 165], "[2] B. Costa, Another article title, 2021."),
    ])]
    refs = catalog_references(pages)["references"]
    assert len(refs) == 2
    assert refs[0]["title"] == "A title continuing across pages"
    assert (refs[0]["page_start"], refs[0]["page_end"]) == (1, 2)
    assert all("Synthetic Authors" not in ref["raw_text"] for ref in refs)


def test_initials_hyphenated_unicode_authors_and_title_punctuation():
    page = layout_page(1, [([40, 100, 540, 190],
        "References\n[1] P.-H. Silva, F. İnan, J. van Dijk, Security, privacy, "
        "and ethics vs. practice, Example Journal 3 (2024) 1-8.\n"
        "[2] EXAMPLE, Example technical report, 2023, https://example.org/report.")])
    refs = catalog_references([page])["references"]
    assert refs[0]["authors"] == ["P.-H. Silva", "F. İnan", "J. van Dijk"]
    assert refs[0]["title"] == "Security, privacy, and ethics vs. practice"
    assert refs[0]["year"] == 2024
    assert refs[1]["authors"] == ["EXAMPLE"]
    assert refs[1]["title"] == "Example technical report"


def test_incomplete_geometry_falls_back_without_losing_uncovered_text():
    page = layout_page(1, [([40, 100, 270, 130],
        "References\n[1] A. Silva, First article title, 2020.")])
    page["normalized_text"] += "\n[2] B. Costa, Second article title, 2021."
    refs = catalog_references([page])["references"]
    assert [ref["entry_number"] for ref in refs] == [1, 2]


def test_numbered_apa_reference_keeps_author_date_boundary():
    page = layout_page(1, [([40, 100, 540, 160],
        "References\n[1] Costa, A. (2022). A synthetic article title. Example Journal, 3, 1-9.")])
    ref = catalog_references([page])["references"][0]
    assert ref["authors"] == ["Costa, A"]
    assert ref["title"] == "A synthetic article title"
    assert ref["year"] == 2022

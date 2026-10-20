import io
import shutil

import pymupdf
import pytest

from packages.document_processing.bibliography import catalog_references, map_citations
from packages.document_processing.chunks import chunk_pages
from packages.document_processing.pdf import PDFValidationError, extract_pdf, validate_pdf
from packages.document_processing.report import render_report, snapshot_totals


def page(number, text, status="EXTRACTED"):
    return {"physical_index": number - 1, "physical_page_number": number, "printed_label": None,
            "raw_text": text, "normalized_text": text, "status": status, "extraction_method": "NATIVE",
            "blocks": [], "quality": {}, "error": None}


def pdf_bytes(text="Texto científico para extração nativa. " * 10, image=False, password=None):
    with pymupdf.open() as doc:
        p = doc.new_page()
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 730), text)
        p.insert_text((295, 810), "iv")
        if image:
            png = p.get_pixmap().tobytes("png")
            with pymupdf.open() as scanned:
                scanned.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=png)
                return scanned.tobytes()
        if password:
            return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw=password)
        return doc.tobytes()


def test_pdf_validation_signatures_limits_and_password():
    valid = pdf_bytes()
    assert validate_pdf(valid, "tese.PDF", "application/pdf")["page_count"] == 1
    cases = [(b"bad", "x.pdf", "application/pdf", "INVALID_PDF_SIGNATURE"),
             (valid, "x.exe", "application/pdf", "INVALID_EXTENSION"),
             (valid, "x.pdf", "image/png", "INVALID_CONTENT_TYPE"),
             (b"%PDF-1.7\nbroken", "x.pdf", "application/pdf", "CORRUPT_PDF"),
             (pdf_bytes(password="secret"), "x.pdf", "application/pdf", "PASSWORD_PROTECTED")]
    for data, filename, mime, code in cases:
        with pytest.raises(PDFValidationError) as error:
            validate_pdf(data, filename, mime)
        assert error.value.code == code
    with pytest.raises(PDFValidationError, match="limite"):
        validate_pdf(valid, "x.pdf", "application/pdf", max_bytes=1)


def test_native_pdf_printed_number_and_normalized_offsets():
    extracted = extract_pdf(pdf_bytes(), ocr_enabled=False)
    p = extracted["pages"][0]
    assert p["physical_index"] == 0 and p["physical_page_number"] == 1
    assert p["printed_label"] == "iv"
    assert p["status"] == "EXTRACTED"
    for block in p["blocks"]:
        assert p["normalized_text"][block["offset_start"]:block["offset_end"]].strip()


def test_wrapped_sentences_keep_all_words_and_drop_folio():
    text = "Silva (2024) observou aumento de 12% na atenção em 40 adultos após pausas de cinco minutos."
    with pymupdf.open() as doc:
        p = doc.new_page()
        p.insert_textbox(pymupdf.Rect(60, 55, 535, 750), text, fontsize=12, lineheight=1.5)
        p.insert_text((290, 805), "i")
        p = doc.new_page()
        p.insert_text((60, 60), "Referências\nSILVA, Ana. Estudo. 2024.")
        extracted = extract_pdf(doc.tobytes(), ocr_enabled=False)
    mapped = map_citations(extracted["pages"], catalog_references(extracted["pages"]))
    assert mapped["occurrences"][0]["sentence"] == text


def test_scanned_page_failed_ocr_is_not_success(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic OCR failure")
    monkeypatch.setattr("packages.document_processing.pdf._ocr", broken)
    result = extract_pdf(pdf_bytes(image=True))
    assert result["pages"][0]["status"] == "UNVERIFIABLE"
    assert result["pages"][0]["error"]
    assert result["warnings"]


def test_mixed_pdf_ocr_only_where_needed(monkeypatch):
    calls = []
    def ocr(p, *args):
        calls.append(p.number)
        return "Texto recuperado por OCR sintético.", [], .9
    monkeypatch.setattr("packages.document_processing.pdf._ocr", ocr)
    with pymupdf.open(stream=pdf_bytes(), filetype="pdf") as native, pymupdf.open(stream=pdf_bytes(image=True), filetype="pdf") as scan:
        native.insert_pdf(scan)
        result = extract_pdf(native.tobytes())
    assert calls == [1]
    assert [p["extraction_method"] for p in result["pages"]] == ["NATIVE", "OCR"]


def test_extraction_deadline_accounts_for_every_unverified_page():
    with pymupdf.open(stream=pdf_bytes(), filetype="pdf") as doc:
        doc.new_page()
        result = extract_pdf(doc.tobytes(), max_seconds=0)
    assert len(result["pages"]) == 2
    assert all(p["status"] == "UNVERIFIABLE" and "Limite total" in p["error"] for p in result["pages"])
    assert len(result["warnings"]) == 1


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract real não está instalado neste ambiente")
def test_actual_tesseract_scanned_fixture():
    result = extract_pdf(pdf_bytes("Synthetic fixture. Silva 2024. Attention study with forty adults." * 8, image=True), languages="eng")
    assert result["pages"][0]["extraction_method"] == "OCR"
    assert "Silva" in result["pages"][0]["normalized_text"]


def test_catalog_required_before_mapping():
    with pytest.raises(ValueError, match="concluído"):
        map_citations([page(1, "Silva (2020)")], {"references": []})


def test_author_date_duplicate_ambiguity_missing_and_page_coverage():
    pages = [
        page(1, "Silva (2020) observou melhora. Costa (1999) relata algo."),
        page(2, ""),
        page(3, "", "UNVERIFIABLE"),
        page(4, "Referências\nSILVA, Ana. Estudo de teste. Editora, 2020.\nSILVA, Ana. Estudo de teste. Editora, 2020."),
    ]
    catalog = catalog_references(pages, scope="run")
    assert len(catalog["references"]) == 2
    assert all("DUPLICATE_REFERENCE" in r["issues"] for r in catalog["references"])
    mapped = map_citations(pages, catalog)
    assert len(mapped["pages"]) == 4
    assert [o["binding_status"] for o in mapped["occurrences"]] == ["AMBIGUOUS", "MISSING_REFERENCE"]
    assert [p["status"] for p in mapped["pages"]] == ["ANALYZED", "NO_CITATIONS", "UNVERIFIABLE", "BIBLIOGRAPHIC"]


def test_bibliography_region_excludes_only_its_part_of_page():
    pages = [page(1, "Silva (2020) observou melhora.\n\nReferências\nSILVA, Ana. Estudo. 2020.")]
    catalog = catalog_references(pages)
    mapped = map_citations(pages, catalog)
    assert len(mapped["occurrences"]) == 1
    assert mapped["pages"][0]["status"] == "ANALYZED"


def test_numeric_groups_ranges_and_equations():
    pages = [page(1, "Estudos observam melhora [1, 3–4]. A equação [2] é definida."),
             page(2, "References\n[1] SILVA, Ana. Estudo um. 2020.\n[2] COSTA, Ana. Estudo dois. 2021.\n[3] LIMA, Ana. Estudo três. 2022.\n[4] SOUZA, Ana. Estudo quatro. 2023.")]
    mapped = map_citations(pages, catalog_references(pages))
    assert len(mapped["occurrences"]) == 1
    assert len(mapped["occurrences"][0]["reference_ids"]) == 3
    assert len(mapped["claims"]) == 1


def test_cross_page_marker_group_year_suffix_and_apud():
    pages = [page(1, "Uma observação de Silva ("), page(2, "2020a) descreve melhora.\n\nO relato (Silva, 2020a apud Costa, 2021) é secundário."),
             page(3, "Referências\nSILVA, Ana. Estudo original. 2020a.\nCOSTA, Bia. Relato. 2021.")]
    mapped = map_citations(pages, catalog_references(pages))
    assert len(mapped["occurrences"]) == 2
    assert mapped["occurrences"][0]["page_start"] == 1
    assert mapped["occurrences"][0]["page_end"] == 2
    assert mapped["occurrences"][1]["metadata"]["secondary_citation"]
    assert len(mapped["occurrences"][1]["reference_ids"]) == 2


def test_missing_section_is_not_invented_and_manual_range_works():
    pages = [page(1, "Silva (2020) observou melhora."),
             page(2, "SILVA, Ana. Estudo. 2020.")]
    assert catalog_references(pages)["references"] == []
    catalog = catalog_references(pages, page_range=[2, 2])
    assert len(catalog["references"]) == 1
    assert map_citations(pages, catalog)["occurrences"][0]["binding_status"] == "RESOLVED"


def test_undated_and_grouped_citation():
    pages = [page(1, "Estudos (Silva, s.d.; Costa, 2021) descrevem o fenômeno."),
             page(2, "Referências\nSILVA, Ana. Estudo sem data. s.d.\nCOSTA, Bia. Relato. 2021.")]
    mapped = map_citations(pages, catalog_references(pages))
    assert len(mapped["occurrences"]) == 1
    assert len(mapped["occurrences"][0]["reference_ids"]) == 2


def test_institutional_et_al_multiple_authors_and_unicode_superscripts():
    pages = [page(1, "ORGANIZAÇÃO MUNDIAL DA SAÚDE (2020) apresenta orientações.\n\n"
                    "Silva e Costa (2021) relatam um efeito. Silva et al. (2021) discutem esse resultado.\n\n"
                    "Os estudos descrevem um efeito¹."),
             page(2, "Referências\n[1] ORGANIZAÇÃO MUNDIAL DA SAÚDE. Relatório sintético. 2020.\n"
                     "[2] SILVA, Ana; COSTA, Bia. Estudo conjunto. 2021.")]
    mapped = map_citations(pages, catalog_references(pages))
    assert len(mapped["occurrences"]) == 4
    assert all(o["binding_status"] == "RESOLVED" for o in mapped["occurrences"])
    assert mapped["occurrences"][0]["marker"].startswith("ORGANIZAÇÃO")
    assert mapped["occurrences"][-1]["kind"] == "SUPERSCRIPT"


def test_ampersand_authors_and_mixed_case_institution_do_not_create_false_bindings():
    pages = [page(1, "Silva & Santos (2020) relatam um efeito.\n\n"
                    "O efeito (Silva & Santos, 2020) foi observado.\n\n"
                    "World Health Organization (2020) descreve orientações.\n\n"
                    "O relato (World, 2020) não identifica a instituição.")]
    catalog = {"status": "COMPLETED", "bibliography_regions": [], "references": [
        {"id": "pair", "authors": ["Silva, Ana", "Santos, Bia"], "year": 2020, "year_suffix": None},
        {"id": "institution", "authors": ["World Health Organization"], "year": 2020, "year_suffix": None},
    ]}
    mapped = map_citations(pages, catalog)
    assert len(mapped["occurrences"]) == 4
    assert [o["binding_status"] for o in mapped["occurrences"]] == [
        "RESOLVED", "RESOLVED", "RESOLVED", "MISSING_REFERENCE"]


def test_deterministic_ids_and_chunk_scope_isolation():
    pages = [page(1, "Uma passagem com texto relevante. " * 150)]
    kwargs = dict(origin="MANUSCRIPT", document_id="doc", document_version_id="v1", user_id="u1")
    first = chunk_pages(pages, **kwargs)["chunks"]
    assert first == chunk_pages(pages, **kwargs)["chunks"]
    assert first[0]["id"] != chunk_pages(pages, **{**kwargs, "user_id": "u2"})["chunks"][0]["id"]
    assert len(first) == 1
    for chunk in first:
        assert chunk["text"] == pages[0]["normalized_text"][chunk["offset_start"]:chunk["offset_end"]]
    with pytest.raises(ValueError, match="exigem"):
        chunk_pages(pages, **{**kwargs, "origin": "SOURCE"})


def test_report_real_counts_selectable_text_safe_markup_and_link():
    snapshot = {"demo": True, "document": {"title": "Revisão de atenção <script>alert(1)</script>"},
                "pages": [page(1, "", "UNVERIFIABLE")], "references": [], "occurrences": [],
                "claims": [], "assessments": [],
                "sources": [{"id": "source", "url": "https://example.org/source", "access_status": "RESTRICTED"}],
                "warnings": ["Não foi possível consultar a fonte."]}
    totals = snapshot_totals(snapshot)
    assert totals["pages_processed"] == 0 and totals["pages_total"] == 1
    data = render_report(snapshot)
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        full_text = "\n".join(p.get_text() for p in doc)
        assert "DEMONSTRAÇÃO SINTÉTICA" in full_text
        assert "Revisão de atenção" in full_text
        assert "Não verificável" in full_text
        assert any(link.get("uri") == "https://example.org/source" for p in doc for link in p.get_links())
        for p in doc:
            for block in p.get_text("blocks"):
                assert block[0] >= 0 and block[2] <= p.rect.width + 1
                assert block[1] >= 0 and block[3] <= p.rect.height + 1


def test_report_renders_relational_evidence_and_pending_page_denominator():
    snapshot = {
        "document": {"title": "Snapshot relacional", "version": {"version": 2, "page_count": 5, "content_hash": "abc"}},
        "summary": {"pages_total": 5, "pages_processed": 1, "pairs_total": 2},
        "pages": [page(1, "Texto.", "ANALYZED")],
        "claims": [{"id": "claim", "text": "Afirmação.", "details": {"occurrence_ids": ["occ"], "reference_ids": ["ref"]}}],
        "occurrences": [{"id": "occ", "page_start": 1, "page_end": 1, "marker": "(Silva, 2020)", "reference_ids": ["ref"], "binding_status": "RESOLVED"}],
        "references": [{"id": "ref", "raw_text": "Silva. Estudo. 2020.", "identity_status": "MATCHED"}],
        "assessments": [{"id": "assessment", "claim_id": "claim", "occurrence_id": "occ", "reference_id": "ref",
                         "semantic_status": "INCONCLUSIVE", "rationale": "Cobertura insuficiente."}],
        "evidence": [{"id": "evidence", "text": "Passagem da fonte.", "page_start": 4, "page_end": 4}],
        "assessment_evidence": [{"assessment_id": "assessment", "evidence_id": "evidence", "quote": "Passagem da fonte."}],
    }
    totals = snapshot_totals(snapshot)
    assert totals["pages_total"] == 5 and totals["pairs_identified"] == 2
    assert totals["pairs_evaluated"] == 1 and totals["pairs_decided"] == 0 and totals["inconclusive"] == 1
    with pymupdf.open(stream=render_report(snapshot), filetype="pdf") as doc:
        text = "\n".join(p.get_text() for p in doc)
        assert "Passagem da fonte." in text
        assert "páginas 4" in text


def test_report_same_snapshot_is_byte_deterministic_for_upload_retries(monkeypatch):
    import reportlab.lib.utils
    snapshot = {"document": {"title": "Relatório idempotente"}, "pages": [], "warnings": []}
    monkeypatch.setattr(reportlab.lib.utils.time, "time", lambda: 1_700_000_000)
    first = render_report(snapshot)
    monkeypatch.setattr(reportlab.lib.utils.time, "time", lambda: 1_800_000_000)
    assert first == render_report(snapshot)

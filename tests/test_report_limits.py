"""Product invariants for concise reports, including real PDF pagination."""
import copy
import json
from pathlib import Path

import pymupdf
import pytest

from packages.document_processing import report


def snapshot_with_findings(count, *, repeated=False, long_text=False):
    explanation = "Diagnóstico repetido para revisão." if repeated else "Diagnóstico individual para revisão."
    if long_text:
        explanation += " Texto longo, com contexto e limitações para avaliar a afirmação." * 150
    snapshot = {
        "document": {"title": "Revisão extensa", "version": {"version": 1, "page_count": count}},
        "analysis": {"id": "run-stress"},
        "summary": {"pages_total": count, "pages_processed": count, "pairs_total": count},
        "pages": [], "references": [], "occurrences": [], "claims": [],
        "assessments": [], "sources": [], "evidence": [], "review_actions": [], "warnings": [],
    }
    for index in range(count):
        snapshot["pages"].append({
            "physical_page_number": index + 1, "status": "ANALYZED",
            "extraction_method": "NATIVE", "mapping_complete": True,
        })
        snapshot["references"].append({
            "id": f"ref-{index}", "raw_text": f"Autor {index}. Estudo com dados empíricos. 2025.",
            "identity_status": "MATCHED", "issues": [],
        })
        snapshot["occurrences"].append({
            "id": f"occ-{index}", "page_start": index + 1, "page_end": index + 1,
            "binding_status": "RESOLVED", "reference_ids": [f"ref-{index}"], "marker": f"(Autor {index}, 2025)",
        })
        snapshot["claims"].append({
            "id": f"claim-{index}", "text": f"Afirmação científica {index}. " + (explanation if long_text else ""),
            "occurrence_ids": [f"occ-{index}"], "reference_ids": [f"ref-{index}"],
        })
        snapshot["assessments"].append({
            "id": f"finding-{index}", "claim_id": f"claim-{index}", "occurrence_id": f"occ-{index}",
            "reference_id": f"ref-{index}", "source_id": f"source-{index}",
            "semantic_status": "INCONCLUSIVE" if repeated else "CONTRADICTED",
            "severity": "HIGH", "rationale": explanation if repeated else f"Item {index}. {explanation}",
            "suggested_adjustment": "Revisar a redação e consultar a fonte." + (explanation if long_text else ""),
            "reviewed_scope": "Texto integral consultado.", "limitations": ["Conferir contexto."],
            "evidence": [] if repeated else [{"evidence_id": f"evidence-{index}", "quote": explanation}],
        })
        snapshot["sources"].append({
            "id": f"source-{index}", "reference_id": f"ref-{index}", "title": f"Fonte {index}",
            "identity_status": "MATCHED", "access_status": "FULL_TEXT", "version": 2,
            "url": f"https://example.org/source/{index}", "content_hash": "a" * 64,
        })
        snapshot["evidence"].append({
            "id": f"evidence-{index}", "source_id": f"source-{index}", "version": 2,
            "page_start": 4, "page_end": 5, "section": "Resultados", "text": explanation,
        })
    return snapshot


def pdf_text(data):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return len(document), "\n".join(page.get_text() for page in document)


def test_thousand_repeated_diagnoses_are_grouped_without_inflating_pdf():
    snapshot = snapshot_with_findings(1000, repeated=True)
    page_count, text = pdf_text(report.render_report(snapshot))
    assert page_count <= 6
    assert "1000 pareceres registrados, organizados em 1 grupos" in text
    assert "1000 / 1000" in text
    assert "Diagnóstico repetido para revisão." in text
    assert text.count("Diagnóstico repetido para revisão.") == 1
    assert "1–1000" in text
    assert "Afirmação de exemplo" in text
    assert "1000 referência(s)" in text
    assert "988 fontes sem ficha" in text


def test_thousand_long_findings_use_measured_page_budget_and_keep_summary(monkeypatch):
    snapshot = snapshot_with_findings(1000, long_text=True)
    # Make every supplemental section large as well.
    for index, page in enumerate(snapshot["pages"]):
        page["status"] = "UNVERIFIABLE"
        page["error"] = f"Erro de leitura {index}. " + "Explicação detalhada. " * 150
        snapshot["references"][index]["issues"] = ["MISSING_YEAR"]
        snapshot["occurrences"][index]["binding_status"] = "MISSING_REFERENCE"
        snapshot["warnings"].append(f"Limitação adicional {index}. " + "Contexto. " * 150)
        snapshot["review_actions"].append({"decision": "DISAGREE", "comment": f"Revisão {index}. " + "Contexto. " * 150})
    build = report._render_report
    attempts = []

    def measured(*args, **kwargs):
        data, page_count = build(*args, **kwargs)
        attempts.append(page_count)
        return data, page_count

    monkeypatch.setattr(report, "_render_report", measured)
    data = report.render_report(snapshot)
    page_count, text = pdf_text(data)
    assert attempts[0] > report.MAX_REPORT_PAGES, "Stress fixture must exercise actual re-pagination."
    assert len(attempts) > 1
    assert page_count <= 20
    assert "1000 pareceres registrados" in text
    assert "1000 têm decisão semântica conclusiva" in text
    assert "Discordância: 1000" in text
    assert "Detalhes selecionados:" in text and "pareceres sem detalhe neste PDF" in text
    assert "Limitações e revisão humana" in text
    assert "fonte source-0; versão 2; páginas 4–5" in text
    assert "Snapshot / data" in text
    assert "Limitação adicional 0" in text
    assert "1000 de 1000 marcadores" in text
    assert "[…]" in text
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for page in document:
            for x0, y0, x1, y1, *_ in page.get_text("blocks"):
                assert 0 <= x0 <= x1 <= page.rect.width + 1
                assert 0 <= y0 <= y1 <= page.rect.height + 1


def test_distinct_evidence_and_human_reviews_do_not_collapse_into_one_diagnosis():
    snapshot = snapshot_with_findings(3, repeated=True)
    snapshot["assessments"][0]["evidence"] = [{"evidence_id": "evidence-0", "quote": "Trecho A."}]
    snapshot["assessments"][1]["evidence"] = [{"evidence_id": "evidence-1", "quote": "Trecho B."}]
    snapshot["assessments"][2]["human_review"] = {"decision": "DISAGREE", "comment": "Rever classificação."}
    _, text = pdf_text(report.render_report(snapshot))
    assert "3 pareceres registrados, organizados em 3 grupos" in text
    assert "Trecho A." in text and "Trecho B." in text
    assert "Rever classificação." in text


def test_repeated_quote_is_shared_and_actionable_diagnoses_precede_support():
    snapshot = json.loads((Path(__file__).parent / "fixtures/example-report-snapshot.json").read_text(encoding="utf-8"))
    _, text = pdf_text(report.render_report(snapshot))
    quote = "Estudo sintético com 40 adultos observou aumento de 12%"
    assert text.count(quote) == 1
    assert "Evidência compartilhada" in text
    assert text.index("1. Conteúdo em conflito") < text.index("3. Apoio explícito")
    assert "fonte demo-source" in text


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://[broken", 'data:text/html,<script>alert(1)</script>'])
def test_unsafe_or_malformed_urls_are_text_and_do_not_create_pdf_actions(url):
    snapshot = snapshot_with_findings(1)
    snapshot["sources"][0]["url"] = url
    snapshot["assessments"][0]["rationale"] = '<a href="javascript:evil()">texto</a>'
    snapshot["document"]["title"] = "<b>literal</b>\x01"
    data = report.render_report(snapshot)
    with pymupdf.open(stream=data, filetype="pdf") as document:
        assert not [link for page in document for link in page.get_links()]
        assert "literal" in "".join(page.get_text() for page in document)


def test_rendering_is_deterministic_and_does_not_mutate_original_snapshot():
    snapshot = snapshot_with_findings(20, repeated=True)
    original = copy.deepcopy(snapshot)
    first = report.render_report(snapshot)
    assert first == report.render_report(snapshot)
    assert snapshot == original

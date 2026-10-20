"""Generate owned synthetic fixtures; no external copyrighted texts or web calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from packages.document_processing.bibliography import catalog_references, map_citations
from packages.document_processing.pdf import extract_pdf
from packages.document_processing.report import render_report

DESTINATION = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
DEMO_CLAIMS = [
    "Silva (2024) observou aumento de 12% na atenção em 40 adultos após pausas de cinco minutos.",
    "Silva (2024) demonstrou que pausas de cinco minutos causam aumento de 12% na atenção de toda a população.",
    "Silva (2024) observou redução de 12% na atenção em 40 adultos após pausas de cinco minutos.",
]
DEMO_SOURCE = (
    "Estudo sintético com 40 adultos observou aumento de 12% na atenção após pausas de cinco minutos. "
    "O desenho observacional não demonstra causalidade. "
    "Os resultados não devem ser generalizados para crianças ou para toda a população."
)
DEMO_REFERENCE = "[1] SILVA, Ana. Efeito de pausas na atenção: estudo sintético. CitaLens, 2024. DOI: 10.0000/citalens.demo."


def make_pdf(pages: list[tuple[str, str]], path: Path):
    with pymupdf.open() as doc:
        for text, folio in pages:
            page = doc.new_page(width=595, height=842)
            page.insert_textbox(pymupdf.Rect(60, 55, 535, 750), text, fontsize=12,
                                fontname="helv", lineheight=1.5)
            page.insert_text((290, 805), folio, fontsize=10)
        doc.save(path, no_new_id=True)


def generate() -> dict:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    make_pdf([
        ("DEMONSTRAÇÃO SINTÉTICA\nDocumento criado exclusivamente para testes.\n\n" + DEMO_CLAIMS[0], "i"),
        ("\n\n".join(DEMO_CLAIMS[1:]), "1"),
        ("Referências\n\n" + DEMO_REFERENCE, "2"),
    ], DESTINATION / "demo-manuscript.pdf")
    make_pdf([("FONTE SINTÉTICA DE DEMONSTRAÇÃO\n\n" + DEMO_SOURCE, "1")], DESTINATION / "demo-source.pdf")
    with pymupdf.open(DESTINATION / "demo-manuscript.pdf") as original, pymupdf.open() as scanned, pymupdf.open() as mixed:
        for index, source_page in enumerate(original):
            image = source_page.get_pixmap(matrix=pymupdf.Matrix(1.7, 1.7)).tobytes("png")
            scanned.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), stream=image)
            if index == 1:
                mixed.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), stream=image)
            else:
                mixed.insert_pdf(original, from_page=index, to_page=index)
        scanned.save(DESTINATION / "scanned.pdf", no_new_id=True)
        mixed.save(DESTINATION / "mixed.pdf", no_new_id=True)
        original.save(DESTINATION / "password-protected.pdf",
                      encryption=pymupdf.PDF_ENCRYPT_AES_256,
                      owner_pw="fixture-owner", user_pw="fixture-password")
    (DESTINATION / "corrupted.pdf").write_bytes(b"%PDF-1.7\nsynthetic corruption, no catalog")
    extracted = extract_pdf((DESTINATION / "demo-manuscript.pdf").read_bytes(), ocr_enabled=False)
    catalog = catalog_references(extracted["pages"], scope="demo-report")
    mapping = map_citations(extracted["pages"], catalog, scope="demo-report")
    reference = catalog["references"][0]
    source_id, evidence_id = "demo-source", "demo-evidence"
    evidence = {"id": evidence_id, "source_id": source_id, "text": DEMO_SOURCE,
                "page_start": 1, "page_end": 1, "section": "Resultados sintéticos"}
    annotations = [
        ("SUPPORTED", "LOW", "A fonte sintética informa a mesma amostra, direção e magnitude.", "Manter o recorte de 40 adultos e o caráter observacional."),
        ("PARTIALLY_SUPPORTED", "HIGH", "A magnitude está na fonte, mas causalidade e generalização não foram demonstradas.", "Descrever associação em 40 adultos, sem generalizar para toda a população."),
        ("CONTRADICTED", "HIGH", "A fonte registra aumento de 12%, enquanto a afirmação declara redução de 12%.", "Substituir redução por aumento, preservando amostra e condições."),
    ]
    assessments = []
    for index, occurrence in enumerate(mapping["occurrences"]):
        semantic, severity, rationale, suggestion = annotations[index]
        assessments.append({
            "id": f"annotated-demo-assessment-{index + 1}",
            "claim_id": occurrence["claim_ids"][0], "occurrence_id": occurrence["id"],
            "reference_id": reference["id"], "source_id": source_id,
            "semantic_status": semantic, "identity_status": "MATCHED", "access_status": "FULL_TEXT",
            "rationale": rationale, "severity": severity, "suggested_adjustment": suggestion,
            "evidence": [{"evidence_id": evidence_id, "quote": DEMO_SOURCE}],
            "reviewed_scope": "Parágrafo integral da fonte sintética, página 1.",
            "limitations": ["Parecer anotado manualmente para demonstração; não é pesquisa real."],
        })
    snapshot = {
        "snapshot_version": 1, "created_at": "2026-10-20T12:00:00-03:00", "demo": True,
        "document": {"title": "Manuscrito sintético: pausas e atenção", "version": 1,
                     "sha256": hashlib.sha256((DESTINATION / "demo-manuscript.pdf").read_bytes()).hexdigest()},
        "analysis": {"id": "synthetic-example-not-a-real-analysis"},
        "config": {"mode": "demo", "citation_style": "não validado", "language": "pt-BR"},
        "pages": mapping["pages"], "references": catalog["references"],
        "occurrences": mapping["occurrences"], "claims": mapping["claims"], "assessments": assessments,
        "sources": [{"id": source_id, "reference_id": reference["id"], "title": reference["title"],
                     "identity_status": "MATCHED", "access_status": "FULL_TEXT", "version": "1",
                     "url": "https://example.invalid/synthetic-source", "accessed_at": "fixture; sem acesso externo",
                     "content_hash": hashlib.sha256(DEMO_SOURCE.encode()).hexdigest(),
                     "limitations": ["Fonte fictícia; DOI 10.0000/citalens.demo não é identificador real."]}],
        "evidence": [evidence], "warnings": extracted["warnings"] + catalog["warnings"] + mapping["warnings"],
    }
    report_bytes = render_report(snapshot)
    (DESTINATION / "example-report.pdf").write_bytes(report_bytes)
    with pymupdf.open(stream=report_bytes, filetype="pdf") as rendered:
        report_pages = len(rendered)
        overflow_blocks = sum(
            block[0] < 0 or block[1] < 0 or block[2] > rendered_page.rect.width + 1 or block[3] > rendered_page.rect.height + 1
            for rendered_page in rendered for block in rendered_page.get_text("blocks")
        )
        for index, rendered_page in enumerate(rendered):
            rendered_page.get_pixmap().save(DESTINATION / f"report-preview-{index + 1}.png")
    (DESTINATION / "example-report-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    expected = {
        "license": "CC0-1.0: conteúdo sintético criado neste repositório",
        "demo_is_not_real_research": True, "claims": DEMO_CLAIMS, "reference": DEMO_REFERENCE,
        "source": DEMO_SOURCE, "expected": {"pages": 3, "references": 1, "occurrences": 3,
                                            "printed_labels": ["i", "1", "2"],
                                            "semantic_states": [a[0] for a in annotations]},
        "ocr": "scanned.pdf e mixed.pdf requerem Tesseract real para validar OCR.",
    }
    (DESTINATION / "annotations.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    observed = {"pages_extracted": len(extracted["pages"]), "references_extracted": len(catalog["references"]),
                "occurrences_detected": len(mapping["occurrences"]),
                "resolved_occurrences": sum(o["binding_status"] == "RESOLVED" for o in mapping["occurrences"]),
                "extraction": {"expected_sentences": len(DEMO_CLAIMS),
                               "complete_sentences_recovered": sum(
                                   phrase in re.sub(r"\s+", " ", "\n".join(p["normalized_text"] for p in extracted["pages"]))
                                   for phrase in DEMO_CLAIMS)},
                "binding": {"expected_occurrences": 3, "correct_bindings": sum(
                    o["marker"] == "Silva (2024)" and o["reference_ids"] == [reference["id"]]
                    for o in mapping["occurrences"])},
                "classification": {"measured": False, "reason": "Pareceres anotados manualmente; nenhuma inferência de modelo foi executada pelo gerador."},
                "rendering": {"pages": report_pages, "text_blocks_outside_page": overflow_blocks},
                "semantic_assessments": "Anotações determinísticas de demonstração; nenhuma chamada LLM/web."}
    (DESTINATION / "observed-metrics.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(observed, ensure_ascii=False, indent=2))
    return observed


if __name__ == "__main__":
    generate()

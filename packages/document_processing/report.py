"""Selectable, paginated PDF rendered only from a persisted/versioned snapshot."""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle

MAX_REPORT_PAGES = 20

LABELS = {
    "SUPPORTED": "Apoio explícito", "PARTIALLY_SUPPORTED": "Apoio parcial",
    "CONTRADICTED": "Conteúdo em conflito", "INCONCLUSIVE": "Inconclusivo",
    "NOT_EVALUATED": "Não avaliado",
    "NOT_SUPPORTED_BY_REVIEWED_CONTENT": "Sem apoio no conteúdo consultado",
    "ANALYZED": "Analisada", "NO_CITATIONS": "Sem citações detectadas",
    "BIBLIOGRAPHIC": "Bibliográfica", "PARTIALLY_READABLE": "Parcialmente legível",
    "UNVERIFIABLE": "Não verificável", "EXTRACTED": "Extraída; mapeamento pendente",
    "FULL_TEXT": "Texto integral", "PARTIAL_TEXT": "Trecho ou capítulo",
    "ABSTRACT_ONLY": "Somente resumo", "METADATA_ONLY": "Somente metadados",
    "RESTRICTED": "Acesso restrito", "NOT_FOUND": "Não localizada", "FETCH_ERROR": "Falha técnica",
    "MATCHED": "Identidade confirmada", "METADATA_MISMATCH": "Metadados divergentes",
    "UNRESOLVED": "Identidade não resolvida", "RESOLVED": "Vínculo resolvido",
    "AMBIGUOUS": "Vínculo ambíguo", "MISSING_REFERENCE": "Referência ausente",
    "HIGH": "Alta", "MEDIUM": "Média", "LOW": "Baixa", "INFO": "Informativa",
    "NATIVE": "Texto nativo", "OCR": "OCR", "NONE": "Não extraída",
    "MISSING_AUTHORS": "Autores não identificados", "MISSING_TITLE": "Título não identificado",
    "MISSING_YEAR": "Ano não identificado", "DUPLICATE_REFERENCE": "Entrada bibliográfica duplicada",
    "AGREE": "Concordância", "DISAGREE": "Discordância", "NEEDS_REVIEW": "Revisão necessária",
    "RESOLVE_LINK": "Correção de vínculo",
}


def _font() -> tuple[str, str]:
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ]
    for regular, bold in candidates:
        if Path(regular).is_file() and Path(bold).is_file():
            if "ReportSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ReportSans", regular))
                pdfmetrics.registerFont(TTFont("ReportSansBold", bold))
                pdfmetrics.registerFontFamily("ReportSans", normal="ReportSans", bold="ReportSansBold")
            return "ReportSans", "ReportSansBold"
    return "Helvetica", "Helvetica-Bold"


def _safe(value) -> str:
    if value is None:
        return "Não informado"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return escape(str(value)).replace("\n", "<br/>")


def _compact(value, limit: int = 700) -> str:
    """Bound displayed text, not the persisted evidence or snapshot."""
    if value is None:
        return "Não informado"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        value = "; ".join(dict.fromkeys(value))
    elif isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    text = " ".join(str(value).split())
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def _url(value: str | None) -> str:
    if not value:
        return "Não informado"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return _safe(_compact(value, 180))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return _safe(_compact(value, 180))
    return f'<link href="{escape(value, quote=True)}" color="#126879">{_safe(_compact(value, 180))}</link>'


def _page_ranges(numbers) -> str:
    numbers = sorted({n for n in numbers if isinstance(n, int) and n > 0})
    ranges = []
    for number in numbers:
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return _compact(", ".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges)
                    or "Não informadas", 160)


def snapshot_totals(snapshot: dict) -> dict:
    pages = snapshot.get("pages", [])
    refs = snapshot.get("references", [])
    occurrences = snapshot.get("occurrences", snapshot.get("citations", []))
    claims = snapshot.get("claims", [])
    assessments = snapshot.get("assessments", snapshot.get("findings", []))
    sources = snapshot.get("sources", [])
    summary = snapshot.get("summary", {})
    version_record = snapshot.get("document", {}).get("version", {})
    document_page_count = version_record.get("page_count", len(pages)) if isinstance(version_record, dict) else len(pages)
    identified = {s.get("reference_id") for s in sources if s.get("identity_status") == "MATCHED"}
    evaluated = sum(a.get("semantic_status") not in {"NOT_EVALUATED", None} for a in assessments)
    decided = sum(a.get("semantic_status") not in {"NOT_EVALUATED", "INCONCLUSIVE", None} for a in assessments)
    claims = [{**c.get("details", {}), **c} for c in claims]
    occurrence_by_id = {o["id"]: o for o in occurrences}
    pair_keys = {(c["id"], oid, rid)
                 for c in claims for oid in c.get("occurrence_ids", [])
                 for rid in occurrence_by_id.get(oid, {}).get("reference_ids", [])
                 if rid in c.get("reference_ids", [])}
    return {
        "pages_total": summary.get("pages_total", document_page_count),
        "pages_processed": sum(p.get("status") in {"ANALYZED", "NO_CITATIONS", "BIBLIOGRAPHIC"} for p in pages),
        "pages_accounted": summary.get("pages_processed", sum(bool(p.get("mapping_complete")) or p.get("status") in
                               {"ANALYZED", "NO_CITATIONS", "BIBLIOGRAPHIC", "PARTIALLY_READABLE", "UNVERIFIABLE"}
                               for p in pages)),
        "references_extracted": len(refs),
        "references_identified": sum(r["id"] in identified or r.get("identity_status") == "MATCHED" for r in refs),
        "occurrences_detected": len(occurrences),
        "occurrences_bound": sum(o.get("binding_status") == "RESOLVED" for o in occurrences),
        "pairs_identified": summary.get("pairs_total", len(pair_keys)),
        "pairs_evaluated": evaluated,
        "pairs_decided": decided,
        "inconclusive": sum(a.get("semantic_status") == "INCONCLUSIVE" for a in assessments),
    }


def _render_report(snapshot: dict, detail_limit: int, extra_limit: int) -> tuple[bytes, int]:
    """Build a complete summary; only the bounded examples change between attempts."""
    font, bold = _font()
    buffer = io.BytesIO()
    document = snapshot.get("document", {})
    version_record = document.get("version", {})
    if not isinstance(version_record, dict):
        version_record = {"version": version_record}
    run = snapshot.get("analysis", snapshot.get("run", {}))
    configuration = snapshot.get("config", run.get("configuration", run.get("config", {}))) or {}
    is_demo = snapshot.get("demo", False) or configuration.get("mode") == "demo" or run.get("mode") == "demo"
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font
    styles["Title"].fontName = bold
    styles["Title"].fontSize, styles["Title"].leading = 20, 24
    styles["Heading1"].fontName, styles["Heading1"].fontSize = bold, 13
    styles["Heading1"].spaceBefore, styles["Heading1"].spaceAfter = 11, 6
    styles["Heading2"].fontName, styles["Heading2"].fontSize = bold, 10
    styles["Heading2"].spaceBefore, styles["Heading2"].spaceAfter = 8, 4
    styles["BodyText"].fontSize, styles["BodyText"].leading = 9, 12
    styles["BodyText"].spaceAfter = 4
    styles.add(ParagraphStyle("SmallReport", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle("TableReport", parent=styles["BodyText"], fontSize=8, leading=10,
                              spaceAfter=0, wordWrap="CJK"))
    styles.add(ParagraphStyle("HeaderCell", parent=styles["TableReport"],
                              textColor=colors.white, fontName=bold))
    story = []

    def paragraph(value, style="BodyText", limit=900):
        return Paragraph(_safe(_compact(value, limit)), styles[style])

    def field(label, value, limit=700, style="BodyText"):
        if value not in (None, "", [], {}):
            story.append(Paragraph(f"<b>{_safe(_compact(label, 180))}:</b> {_safe(_compact(value, limit))}", styles[style]))

    def heading(title):
        story.append(paragraph(title, "Heading1"))

    def table(rows, widths):
        prepared = [[paragraph(cell, "HeaderCell" if row_index == 0 else "TableReport", 220)
                     for cell in row] for row_index, row in enumerate(rows)]
        item = Table(prepared, colWidths=widths, repeatRows=1, hAlign="LEFT")
        item.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173c45")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f0f5f5"), colors.white]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(item)

    def counts(values):
        counter = Counter(values)
        ranked = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
        result = "; ".join(f"{LABELS.get(key, _compact(key, 60))}: {number}" for key, number in ranked[:12])
        if len(ranked) > 12:
            result += f"; outros: {sum(number for _, number in ranked[12:])}"
        return result or "Nenhum registro."

    pages = snapshot.get("pages", [])
    refs = snapshot.get("references", [])
    occurrences = snapshot.get("occurrences", snapshot.get("citations", []))
    assessments = snapshot.get("assessments", snapshot.get("findings", []))
    sources = snapshot.get("sources", [])
    ref_by_id = {r["id"]: r for r in refs}
    claim_by_id = {c["id"]: c for c in snapshot.get("claims", [])}
    occurrence_by_id = {o["id"]: o for o in occurrences}
    source_by_id = {s["id"]: s for s in sources}
    source_by_ref = {}
    for source in sources:
        source_by_ref.setdefault(source.get("reference_id"), source)
    evidence_by_id = {e["id"]: e for e in snapshot.get("evidence", snapshot.get("evidence_passages", []))}
    page_by_number = {p["physical_page_number"]: p for p in pages}
    relational_quotes = defaultdict(list)
    for quote in snapshot.get("assessment_evidence", []):
        relational_quotes[quote.get("assessment_id")].append(quote)

    def source_for(assessment):
        return source_by_id.get(assessment.get("source_id")) or source_by_ref.get(assessment.get("reference_id"), {})

    def quotes_for(assessment):
        return assessment.get("evidence") or relational_quotes.get(assessment.get("id"), [])

    def page_for(assessment):
        return occurrence_by_id.get(assessment.get("occurrence_id"), {}).get("page_start",
                                                                          assessment.get("page_start"))

    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    status_rank = {"CONTRADICTED": 0, "NOT_SUPPORTED_BY_REVIEWED_CONTENT": 1,
                   "PARTIALLY_SUPPORTED": 2, "INCONCLUSIVE": 3, "NOT_EVALUATED": 4, "SUPPORTED": 5}

    def priority(assessment):
        status = assessment.get("semantic_status")
        # Confirmed support follows actionable or unresolved diagnoses, even if a severity is missing.
        return (status == "SUPPORTED", severity_rank.get(assessment.get("severity"), 4),
                status_rank.get(status, 4), page_for(assessment) or 10**9, str(assessment.get("id", "")))

    grouped = {}
    for assessment in sorted(assessments, key=priority):
        source = source_for(assessment)
        # Repeated diagnoses may concern different claims. Group only equal conclusions,
        # scope, limitations and evidence; explicitly label the displayed claim as an example.
        key = json.dumps({
            "status": assessment.get("semantic_status"), "severity": assessment.get("severity"),
            "rationale": assessment.get("rationale", assessment.get("justification")),
            "scope": assessment.get("reviewed_scope", assessment.get("scope")),
            "adjustment": assessment.get("suggested_adjustment", assessment.get("suggestion")),
            "alternative": assessment.get("alternative_text"),
            "limitations": assessment.get("limitations"),
            "identity": assessment.get("identity_status", source.get("identity_status")),
            "access": assessment.get("access_status", source.get("access_status")),
            "binding": occurrence_by_id.get(assessment.get("occurrence_id"), {}).get("binding_status",
                                                                                     assessment.get("binding_status")),
            "quotes": sorted((str(q.get("evidence_id", "")), str(q.get("quote", ""))) for q in quotes_for(assessment)),
            "reviews": assessment.get("human_review") or assessment.get("review_actions") or assessment.get("reviews"),
        }, ensure_ascii=False, sort_keys=True, default=str)
        grouped.setdefault(key, []).append(assessment)
    groups = list(grouped.values())
    selected = groups[:detail_limit]
    represented = sum(len(group) for group in selected)
    totals = snapshot_totals(snapshot)

    story.append(paragraph("Autobibliograph Revision", "Title"))
    story.append(paragraph("Revisão bibliográfica · relatório resumido", "Heading2"))
    if is_demo:
        story.append(paragraph("DEMONSTRAÇÃO SINTÉTICA — fontes e pareceres de teste; não representa pesquisa web real."))
    field("Documento", document.get("title", document.get("filename", "Documento sem título")), 240)
    field("Versão / análise", f"{version_record.get('version', snapshot.get('document_version_id', '—'))} / "
          f"{run.get('id', snapshot.get('analysis_id', '—'))}", 180, "SmallReport")
    report_record = snapshot.get("report", {})
    field("Snapshot / data", f"{snapshot.get('snapshot_version', report_record.get('version', snapshot.get('version', '1')))} / "
          f"{snapshot.get('created_at', report_record.get('created_at', run.get('updated_at', run.get('created_at', '—'))))}",
          180, "SmallReport")
    field("SHA-256 do original", document.get("sha256", document.get("content_hash", version_record.get("content_hash"))),
          100, "SmallReport")

    heading("Resumo executivo")
    story.append(paragraph(
        f"{len(assessments)} pareceres registrados, organizados em {len(groups)} grupos de diagnóstico. "
        f"{totals['pairs_decided']} têm decisão semântica conclusiva; {totals['inconclusive']} são inconclusivos. "
        "Itens não avaliados ou inconclusivos não são considerados corretos."))
    field("Resultados", counts(a.get("semantic_status", "NOT_EVALUATED") for a in assessments), 1000)
    field("Prioridades", counts(a.get("severity", "INFO") for a in assessments))
    table([
        ["Cobertura observada", "Resultado / universo"],
        ["Páginas processadas integralmente", f"{totals['pages_processed']} / {totals['pages_total']}"],
        ["Páginas com resultado ou motivo registrado", f"{totals['pages_accounted']} / {totals['pages_total']}"],
        ["Referências com identidade confirmada", f"{totals['references_identified']} / {totals['references_extracted']}"],
        ["Ocorrências com vínculo resolvido", f"{totals['occurrences_bound']} / {totals['occurrences_detected']}"],
        ["Pares avaliados", f"{totals['pairs_evaluated']} / {totals['pairs_identified']}"],
    ], [118 * mm, 52 * mm])
    story.append(paragraph(
        f"Este PDF tem limite de {MAX_REPORT_PAGES} páginas. Diagnósticos idênticos são agrupados; textos longos "
        "marcados com […] são excertos. Os exemplos priorizam problemas e pendências. "
        "A íntegra dos pareceres, evidências, fontes e cobertura permanece na interface da análise.",
        "SmallReport"))
    if represented < len(assessments):
        story.append(paragraph(
            f"Detalhes selecionados: {len(selected)} de {len(groups)} grupos, representando {represented} de "
            f"{len(assessments)} pareceres. {len(assessments) - represented} pareceres sem detalhe neste PDF "
            "continuam incluídos nos totais acima. Consulte-os na interface.", "SmallReport"))

    heading("Cobertura e limitações de leitura")
    coverage = defaultdict(list)
    for page in pages:
        coverage[page.get("status", "EXTRACTED")].append(page["physical_page_number"])
    coverage_rows = sorted(coverage.items(), key=lambda item: str(item[0]))
    if coverage_rows:
        table([["Resultado", "Quantidade", "Páginas do arquivo"]] + [
            [LABELS.get(status, status), len(numbers), _page_ranges(numbers)]
            for status, numbers in coverage_rows[:12]
        ] + ([["Outros resultados", sum(len(numbers) for _, numbers in coverage_rows[12:]), "Íntegra na interface"]]
             if len(coverage_rows) > 12 else []), [75 * mm, 23 * mm, 72 * mm])
    else:
        story.append(paragraph("Nenhuma página mapeada neste snapshot."))
    missing_pages = max(0, totals["pages_total"] - len(page_by_number))
    if missing_pages:
        field("Páginas ainda sem registro", missing_pages)
    page_errors = defaultdict(list)
    for page in pages:
        if page.get("error"):
            page_errors[str(page["error"])].append(page["physical_page_number"])
    for message, numbers in list(page_errors.items())[:extra_limit]:
        field(f"Limitação · páginas {_page_ranges(numbers)}", message, 350)
    if len(page_errors) > extra_limit:
        story.append(paragraph(f"Mais {len(page_errors) - extra_limit} motivos de limitação na interface.", "SmallReport"))

    heading("Pareceres prioritários")
    if not assessments:
        story.append(paragraph("Nenhum parecer semântico neste snapshot."))
    shown_refs, shown_quotes, used_sources = {}, {}, []
    for index, group in enumerate(selected, 1):
        assessment = group[0]
        occurrence = occurrence_by_id.get(assessment.get("occurrence_id"), {})
        reference = ref_by_id.get(assessment.get("reference_id"), {})
        claim = claim_by_id.get(assessment.get("claim_id"), {})
        source = source_for(assessment)
        if source.get("id") and source["id"] not in used_sources:
            used_sources.append(source["id"])
        status = LABELS.get(assessment.get("semantic_status"), assessment.get("semantic_status", "Não avaliado"))
        severity = LABELS.get(assessment.get("severity"), assessment.get("severity", "Não informada"))
        story.append(paragraph(f"{index}. {status} · prioridade {severity} · {len(group)} parecer(es)", "Heading2"))
        field("Páginas do arquivo", _page_ranges(page_for(a) for a in group), 180)
        page_number = page_for(assessment)
        printed = page_by_number.get(page_number, {}).get("printed_label")
        if printed:
            field("Exemplo · página / rótulo impresso", f"{page_number} / {printed}", 100, "SmallReport")
        field("Parecer de exemplo" if len(group) > 1 else "Parecer", assessment.get("id"), 160, "SmallReport")
        if len(group) > 1:
            affected_refs = {a.get("reference_id") for a in group if a.get("reference_id")}
            field("Abrangência do grupo", f"{len(affected_refs)} referência(s); afirmação e fonte abaixo são do exemplo.")
        field("Afirmação de exemplo" if len(group) > 1 else "Afirmação", claim.get("text", assessment.get("claim_text", assessment.get("claim"))), 600)
        reference_text = reference.get("raw_text", assessment.get("reference_text"))
        reference_key = assessment.get("reference_id") or reference_text
        if reference_text:
            if reference_key not in shown_refs:
                shown_refs[reference_key] = f"R{len(shown_refs) + 1}"
                field(f"Referência {shown_refs[reference_key]}", reference_text, 360)
            else:
                field("Referência", shown_refs[reference_key])
        field("Marcador", occurrence.get("marker", assessment.get("marker")), 120)
        field("Vínculo / identidade / acesso", " / ".join(str(LABELS.get(v, v or "Não informado")) for v in [
            occurrence.get("binding_status", assessment.get("binding_status")),
            assessment.get("identity_status", source.get("identity_status")),
            assessment.get("access_status", source.get("access_status"))]), 300, "SmallReport")
        if source.get("id"):
            field("Fonte / versão", f"{source['id']} / {source.get('version', '—')}", 180, "SmallReport")
        field("Diagnóstico", assessment.get("rationale", assessment.get("justification")), 1000)
        field("Ajuste sugerido", assessment.get("suggested_adjustment", assessment.get("suggestion")), 600)
        field("Redação alternativa", assessment.get("alternative_text"), 500)
        field("Escopo consultado", assessment.get("reviewed_scope", assessment.get("scope")), 300, "SmallReport")
        quotes = quotes_for(assessment)
        for quoted in quotes[:2]:
            passage = evidence_by_id.get(quoted.get("evidence_id"), {})
            quote = quoted.get("quote", passage.get("text"))
            quote_key = (quoted.get("evidence_id"), str(quote))
            if quote_key in shown_quotes:
                field("Evidência compartilhada", shown_quotes[quote_key], 120, "SmallReport")
                continue
            quote_label = f"E{len(shown_quotes) + 1} (parecer {index})"
            shown_quotes[quote_key] = quote_label
            field(f"Passagem {quote_label}", quote, 700)
            field("Localização na fonte",
                  f"ID {quoted.get('evidence_id', '—')}; fonte {passage.get('source_id', source.get('id', '—'))}; "
                  f"versão {passage.get('version', source.get('version', '—'))}; "
                  f"páginas {passage.get('page_start', '—')}–{passage.get('page_end', '—')}; "
                  f"seção {_compact(passage.get('section', '—'), 100)}", 420, "SmallReport")
        if len(quotes) > 2:
            story.append(paragraph(f"Mais {len(quotes) - 2} passagens deste parecer na interface.", "SmallReport"))
        field("Limitações", assessment.get("limitations"), 400, "SmallReport")
        reviews = assessment.get("human_review") or assessment.get("review_actions") or assessment.get("reviews")
        field("Revisão humana preservada", reviews, 400, "SmallReport")
    no_evidence = sum(not quotes_for(a) for a in assessments)
    if no_evidence:
        story.append(paragraph(f"{no_evidence} de {len(assessments)} pareceres não têm evidência textual anexada.", "SmallReport"))

    heading("Vínculos e bibliografia")
    problems = [o for o in occurrences if o.get("binding_status") != "RESOLVED"]
    # An ambiguous candidate is not a resolved citation in the inverse check.
    cited = {rid for o in occurrences if o.get("binding_status") == "RESOLVED" for rid in o.get("reference_ids", [])}
    uncited = [r for r in refs if r["id"] not in cited]
    problematic_refs = [r for r in refs if r.get("issues")]
    story.append(paragraph(
        f"{len(problems)} de {len(occurrences)} marcadores com vínculo pendente; "
        f"{len(uncited)} de {len(refs)} referências sem ocorrência resolvida detectada; "
        f"{len(problematic_refs)} entradas com sinais bibliográficos para revisão. "
        "Ausência de ocorrência detectada não demonstra erro na referência."))
    if problems:
        field("Pendências por tipo", counts(o.get("binding_status", "UNRESOLVED") for o in problems))
        binding_groups = defaultdict(list)
        for occurrence in problems:
            binding_groups[(occurrence.get("binding_status"), occurrence.get("marker"))].append(occurrence)
        for (status, marker), items in list(binding_groups.items())[:extra_limit]:
            field(f"{LABELS.get(status, status)} · {len(items)} ocorrência(s)",
                  f"{_compact(marker, 120)}; páginas {_page_ranges(o.get('page_start') for o in items)}", 350)
        if len(binding_groups) > extra_limit:
            story.append(paragraph(f"Mais {len(binding_groups) - extra_limit} grupos de vínculos na interface.", "SmallReport"))
    if problematic_refs:
        field("Sinais bibliográficos", counts(issue for r in problematic_refs for issue in r.get("issues", [])))
    bibliography_details = sorted(problematic_refs + [r for r in uncited if not r.get("issues")],
                                  key=lambda r: (not bool(r.get("issues")), str(r.get("id", ""))))
    for reference in bibliography_details[:extra_limit]:
        issues = ", ".join(LABELS.get(issue, issue) for issue in reference.get("issues", []))
        suffix = ("; sem ocorrência resolvida" if reference["id"] not in cited else "")
        field(f"Referência · {_compact(reference['id'], 80)}",
              f"{_compact(reference.get('raw_text'), 280)}; {issues}{suffix}", 500, "SmallReport")
    if len(bibliography_details) > extra_limit:
        story.append(paragraph(f"Mais {len(bibliography_details) - extra_limit} entradas para revisão na interface.", "SmallReport"))

    heading("Fontes e proveniência")
    field("Disponibilidade de todas as fontes", counts(s.get("access_status", "NOT_FOUND") for s in sources), 1000)
    ordered_sources = [source_by_id[sid] for sid in used_sources] + [s for s in sources if s["id"] not in used_sources]
    for source in ordered_sources[:extra_limit]:
        field("Fonte", f"{_compact(source.get('title', source.get('id')), 180)} · "
              f"ID {source.get('id')} · versão {source.get('version', '—')}", 350, "SmallReport")
        field("Identidade / acesso", f"{LABELS.get(source.get('identity_status'), source.get('identity_status', 'Não informado'))} / "
              f"{LABELS.get(source.get('access_status'), source.get('access_status', 'Não informado'))}", 250, "SmallReport")
        urls = [source.get("final_url"), source.get("origin_url"), source.get("url")]
        if source.get("doi"):
            urls.append("https://doi.org/" + source["doi"])
        for url in list(dict.fromkeys(url for url in urls if url))[:2]:
            story.append(Paragraph(_url(url), styles["SmallReport"]))
        field("Acesso / hash", f"{source.get('accessed_at', source.get('retrieved_at', '—'))} / "
              f"{source.get('content_hash', source.get('sha256', '—'))}", 180, "SmallReport")
        field("Restrições", source.get("limitations"), 300, "SmallReport")
    if len(sources) > extra_limit:
        story.append(paragraph(f"{len(sources) - extra_limit} fontes sem ficha neste PDF; identificadores das evidências "
                               "selecionadas foram mantidos. Fichas completas na interface.", "SmallReport"))

    heading("Limitações e revisão humana")
    reviews = snapshot.get("review_actions", [])
    if reviews:
        field("Decisões humanas", counts(r.get("decision", "NEEDS_REVIEW") for r in reviews))
        review_groups = Counter((r.get("decision"), str(r.get("comment") or "")) for r in reviews)
        for (decision, comment), count in list(review_groups.items())[:extra_limit]:
            field(f"{LABELS.get(decision, decision)} · {count} registro(s)", comment or "Sem comentário.", 350)
        if len(review_groups) > extra_limit:
            story.append(paragraph(f"Mais {len(review_groups) - extra_limit} grupos de revisões na interface.", "SmallReport"))
        story.append(paragraph("Decisões humanas preservam o parecer original; não substituem automaticamente sua conclusão.", "SmallReport"))
    warnings = list(dict.fromkeys(str(w) for w in snapshot.get("warnings", []) + snapshot.get("limitations", [])))
    for warning in warnings[:extra_limit]:
        story.append(paragraph("• " + warning, "SmallReport", 350))
    if len(warnings) > extra_limit:
        story.append(paragraph(f"{len(warnings) - extra_limit} limitações adicionais disponíveis na interface.", "SmallReport"))
    story.append(paragraph(
        "Método: bibliografia, mapeamento de citações e verificação de identidade, acesso e apoio semântico são etapas "
        "independentes. DOI e similaridade não comprovam sustentação. Fontes restritas, somente metadados e páginas "
        "ilegíveis limitam conclusões; ausência em buscas não prova inexistência. A revisão apoia decisões acadêmicas, "
        "não certifica veracidade, ausência de plágio ou conformidade normativa integral. Os totais descrevem o snapshot, "
        "não estimam acurácia.", "SmallReport"))
    # Relevant, bounded parameters only: large internal configuration is not useful in the PDF.
    public_config = {key: configuration[key] for key in ("mode", "citation_style", "language", "scope")
                     if key in configuration}
    field("Parâmetros da revisão", public_config, 350, "SmallReport")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#b8cccf"))
        canvas.line(20 * mm, 17 * mm, 190 * mm, 17 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#38575d"))
        canvas.drawString(20 * mm, 12 * mm, "Autobibliograph Revision" + (" · DEMONSTRAÇÃO SINTÉTICA" if is_demo else ""))
        canvas.drawRightString(190 * mm, 12 * mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=19 * mm, bottomMargin=24 * mm,
                            title="Relatório resumido de revisão bibliográfica", author="Autobibliograph Revision",
                            invariant=True)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue(), doc.page


def render_report(snapshot: dict) -> bytes:
    """Measure real pagination and reduce examples, never cut pages or shrink fonts."""
    detail_limit, extra_limit = 48, 12
    while True:
        data, pages = _render_report(snapshot, detail_limit, extra_limit)
        if pages <= MAX_REPORT_PAGES:
            return data
        if detail_limit == extra_limit == 0:
            # The bounded executive summary should fit comfortably. Fail closed if a
            # future layout change breaks the invariant; never publish an oversized PDF.
            raise ValueError("O resumo do relatório excedeu o limite de 20 páginas.")
        ratio = (MAX_REPORT_PAGES - 1) / pages
        detail_limit = max(0, int(detail_limit * ratio) - 1)
        extra_limit = max(0, int(extra_limit * ratio) - 1)

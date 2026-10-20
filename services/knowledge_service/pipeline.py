import hashlib
import re
import uuid
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, func
from packages.common import models as m
from packages.common.config import get_settings
from packages.common.http import call, client
from packages.common.serialization import record
from packages.document_processing.bibliography import catalog_references, map_citations
from packages.contracts.api import STAGES
from packages.contracts.assessment import AssessmentRequest
from packages.providers.assessment import canonical_quote, fit_assessment_context, scope_text


def uid(value):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def scoped_id(run, kind, value):
    return uuid.uuid5(run.id, f"{kind}:{value}")


def rows(db, cls, run_id):
    return db.scalars(select(cls).where(cls.run_id == run_id)).all()


def run_for(db, run_id):
    run = db.get(m.AnalysisRun, uid(run_id))
    if not run:
        raise HTTPException(404, "Análise não encontrada.")
    return run


def ensure_active(db, run):
    db.refresh(run)
    if run.cancel_requested or run.status in {"CANCELLED", "FAILED"}:
        raise HTTPException(409, {"code": "CANCELLED", "message": "Análise interrompida."})
    if run.started_at and (m.now() - run.started_at).total_seconds() > get_settings().max_analysis_seconds:
        raise HTTPException(409, {"code": "TIME_BUDGET", "message": "Limite de duração da análise atingido."})


def checkpoint(db, run_id, stage):
    return db.scalar(select(m.KnowledgeCheckpoint).where(
        m.KnowledgeCheckpoint.run_id == run_id, m.KnowledgeCheckpoint.stage == stage))


def page_data(db, run):
    return [record(p) for p in db.scalars(select(m.Page).where(
        m.Page.document_version_id == run.document_version_id).order_by(m.Page.physical_page_number))]


def refreshed_layout(previous, fresh):
    """Refresh structural metadata without changing offsets of historical text."""
    text = previous.get("normalized_text", "")
    quality = dict(fresh.get("quality") or {})
    if text == fresh.get("normalized_text", ""):
        return {"quality": quality, "blocks": fresh.get("blocks", [])}
    mapped, cursor = [], 0
    fresh_text = fresh.get("normalized_text", "")
    for span in quality.get("prose_paragraphs", []):
        paragraph = fresh_text[span["offset_start"]:span["offset_end"]]
        words = paragraph.split()
        if not words:
            continue
        # A whitespace-only remap cannot start/end in the middle of a token:
        # "Silva" must never become an apparent match inside "DeSilva".
        pattern = r"(?<!\S)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\S)"
        matches = list(re.finditer(pattern, text[cursor:]))
        # Ambiguous or changed OCR cannot safely move an old citation to new text.
        if len(matches) != 1:
            continue
        start, end = cursor + matches[0].start(), cursor + matches[0].end()
        mapped.append({"offset_start": start, "offset_end": end})
        cursor = end
    quality["prose_paragraphs"] = mapped
    # These intervals refer to fresh_text. Keeping them against historical text
    # would misidentify exclusions after a whitespace/OCR change.
    quality.pop("prose_excluded_spans", None)
    quality["prose_filter_version"] = "paragraphs-v1"
    quality["prose_layout_note"] = (
        "Estrutura remapeada sobre o texto preservado; parágrafos sem correspondência única foram excluídos."
    )
    return {"quality": quality, "blocks": previous.get("blocks", [])}


def save_chunks(db, run, pages, origin="MANUSCRIPT", source=None):
    settings = get_settings()
    response = call(settings.chunk_url, "/chunk", {
        "pages": pages, "origin": origin, "document_id": str(run.document_id),
        "document_version_id": str(run.document_version_id), "user_id": str(run.user_id),
        "work_id": str(source.work_id) if source else None,
        "source_id": str(source.id) if source else None, "version": source.version if source else "1",
    })
    chunks = response["chunks"]
    for chunk in chunks:
        if not db.get(m.Chunk, uid(chunk["id"])):
            values = {k: chunk[k] for k in (
                "id", "user_id", "document_id", "document_version_id", "source_id", "work_id", "version",
                "origin", "ordinal", "text", "page_start", "page_end", "offset_start", "offset_end", "content_hash")}
            for k in ("id", "user_id", "document_id", "document_version_id", "source_id", "work_id"):
                values[k] = uid(values[k]) if values[k] else None
            db.add(m.Chunk(**values))
    db.flush()
    warnings = []
    # Persisted chunks are authoritative, independent of index availability.
    for begin in range(0, len(chunks), settings.embedding_batch_size):
        batch = chunks[begin:begin + settings.embedding_batch_size]
        try:
            result = call(settings.vector_url, "/index", {
                "chunks": batch, "user_id": str(run.user_id), "document_id": str(run.document_id)},
                timeout=settings.embedding_timeout + 120)
            indexed = set(result.get("indexed_ids", []))
            for chunk in batch:
                row = db.get(m.Chunk, uid(chunk["id"]))
                row.index_state = "INDEXED" if chunk["id"] in indexed else "PENDING"
                row.embedding = result.get("embedding", {})
            warnings += result.get("limitations", [])
        except Exception:
            warnings.append("Índice vetorial indisponível; passagens persistidas permitem leitura lexical.")
    return list(dict.fromkeys(warnings))


def ingest(db, run):
    version = db.get(m.DocumentVersion, run.document_version_id)
    existing = page_data(db, run)
    warnings = []
    needs_layout = any(not isinstance(page.get("quality"), dict) or
                       page["quality"].get("prose_filter_version") != "paragraphs-v1" for page in existing)
    if len(existing) != version.page_count or needs_layout:
        extracted = call(get_settings().ocr_url, "/extract", {
            "file_id": str(version.file_id), "user_id": str(run.user_id),
            "document_version_id": str(version.id)}, timeout=get_settings().stage_timeout)
        seen = set()
        for page in extracted["pages"]:
            number = page["physical_page_number"]
            seen.add(number)
            old = db.scalar(select(m.Page).where(m.Page.document_version_id == version.id,
                                                 m.Page.physical_page_number == number))
            if old:
                if not isinstance(old.quality, dict) or old.quality.get("prose_filter_version") != "paragraphs-v1":
                    updated = refreshed_layout(record(old), page)
                    old.quality, old.blocks = updated["quality"], updated["blocks"]
                    if old.quality.get("prose_layout_note"):
                        warnings.append(old.quality["prose_layout_note"])
                continue
            values = {k: page.get(k) for k in (
                "physical_index", "physical_page_number", "printed_label", "raw_text", "normalized_text",
                "extraction_method", "blocks", "quality", "status", "error")}
            db.add(m.Page(document_version_id=version.id, **values))
        # Every physical page remains accounted for even if the extractor loses an item.
        for number in range(1, version.page_count + 1):
            if number not in seen:
                db.add(m.Page(document_version_id=version.id, physical_index=number-1,
                              physical_page_number=number, status="UNVERIFIABLE",
                              extraction_method="FAILED", quality={}, error="Extrator não devolveu a página."))
        warnings += extracted.get("warnings", [])
        db.flush()
    pages = page_data(db, run)
    warnings += save_chunks(db, run, pages)
    return {"pages_total": version.page_count, "warnings": warnings}


def catalog(db, run):
    options = run.config
    interval = ([options["bibliography_page_start"], options["bibliography_page_end"]]
                if options.get("bibliography_page_start") else None)
    result = catalog_references(page_data(db, run), page_range=interval, scope=str(run.id))
    for ref in result["references"]:
        values = {k: ref.get(k) for k in (
            "raw_text", "authors", "title", "year", "year_suffix", "doi", "isbn", "url",
            "entry_number", "page_start", "page_end", "issues")}
        values["year"] = str(values["year"]) if values["year"] is not None else None
        db.add(m.ReferenceEntry(id=uid(ref["id"]), run_id=run.id, details=ref.get("metadata", {}), **values))
    return {"references_total": len(result["references"]), "catalog": result,
            "warnings": result.get("warnings", [])}


def mapping(db, run):
    cat = checkpoint(db, run.id, "EXTRACTING_REFERENCES")
    if cat is None:
        raise HTTPException(409, "O catálogo deve estar concluído e persistido antes do mapeamento.")
    result = map_citations(page_data(db, run), cat.result["catalog"], scope=str(run.id))
    ref_ids = {str(x.id) for x in rows(db, m.ReferenceEntry, run.id)}
    for occurrence in result["occurrences"]:
        values = {k: occurrence.get(k, "") for k in (
            "marker", "page_start", "page_end", "offset_start", "offset_end",
            "sentence", "paragraph", "context", "binding_status", "kind")}
        # Cross-page endpoints are recorded separately in details; scalar offsets remain monotonic.
        values["offset_end"] = max(values["offset_start"], values["offset_end"])
        db.add(m.CitationOccurrence(id=uid(occurrence["id"]), run_id=run.id,
                                   details=occurrence.get("metadata", {}), **values))
    db.flush()
    for occurrence in result["occurrences"]:
        resolved = set(occurrence.get("reference_ids", []))
        candidates = set(occurrence.get("candidate_reference_ids", []))
        for ref_id in (resolved | candidates) & ref_ids:
            db.add(m.OccurrenceReference(occurrence_id=uid(occurrence["id"]), reference_id=uid(ref_id),
                                        run_id=run.id, relation="RESOLVED" if ref_id in resolved else "CANDIDATE"))
    occurrences = {str(x.id): x for x in rows(db, m.CitationOccurrence, run.id)}
    for claim in result["claims"]:
        row = m.Claim(id=uid(claim["id"]), run_id=run.id, text=claim["text"],
                      details={k: v for k, v in claim.items() if k not in {"id", "text"}})
        db.add(row)
    db.flush()
    for claim in result["claims"]:
        for occurrence_id in set(claim.get("occurrence_ids", [])) & occurrences.keys():
            db.add(m.ClaimOccurrence(claim_id=uid(claim["id"]), occurrence_id=uid(occurrence_id), run_id=run.id))
        for ref_id in set(claim.get("reference_ids", [])) & ref_ids:
            db.add(m.ClaimReference(claim_id=uid(claim["id"]), reference_id=uid(ref_id), run_id=run.id))
    db.flush()
    # Reapply explicit human decisions into this run only; originals remain unchanged.
    previous_id = run.config.get("copy_sources_from")
    if previous_id:
        previous = run_for(db, previous_id)
        if previous.document_id != run.document_id or previous.user_id != run.user_id:
            raise HTTPException(409, "Histórico incompatível com o documento.")
        for action in sorted(rows(db, m.ReviewAction, previous.id), key=lambda x: x.created_at):
            if not action.occurrence_id or not action.reference_id:
                continue
            old_occ = db.get(m.CitationOccurrence, action.occurrence_id)
            old_ref = db.get(m.ReferenceEntry, action.reference_id)
            new_ref = db.scalar(select(m.ReferenceEntry).where(
                m.ReferenceEntry.run_id == run.id, m.ReferenceEntry.raw_text == old_ref.raw_text))
            matches = [o for o in occurrences.values() if o.marker == old_occ.marker and
                       o.page_start == old_occ.page_start and o.offset_start == old_occ.offset_start]
            if new_ref and len(matches) == 1:
                occ = matches[0]
                for link in db.scalars(select(m.OccurrenceReference).where(
                        m.OccurrenceReference.occurrence_id == occ.id)):
                    db.delete(link)
                db.flush()
                db.add(m.OccurrenceReference(occurrence_id=occ.id, reference_id=new_ref.id,
                                            run_id=run.id, relation="RESOLVED"))
                occ.binding_status = "RESOLVED"
                occ.details = {**occ.details, "human_review_action_id": str(action.id)}
    physical_pages = {p["physical_page_number"]: p for p in page_data(db, run)}
    coverage = {p["physical_page_number"]: p for p in result["pages"]}
    for number, page in physical_pages.items():
        item = coverage.get(number, {"status": "UNVERIFIABLE", "error": "Mapeador não devolveu esta página."})
        db.add(m.PageCoverage(run_id=run.id, physical_page_number=number,
                              status=item["status"], error=item.get("error"), details=item))
    return {"pages_processed": len(physical_pages), "occurrences_total": len(occurrences),
            "warnings": result.get("warnings", [])}


def ensure_work(db, run, ref):
    if ref.work_id:
        return db.get(m.BibliographicWork, ref.work_id)
    doi = ref.doi.lower().strip().removeprefix("https://doi.org/") if ref.doi else None
    work = db.scalar(select(m.BibliographicWork).where(
        m.BibliographicWork.document_id == run.document_id,
        m.BibliographicWork.normalized_doi == doi)) if doi else None
    if not work:
        key = doi or ref.raw_text
        work = m.BibliographicWork(id=uuid.uuid5(run.document_id, key), document_id=run.document_id,
                                  user_id=run.user_id, normalized_doi=doi, title=ref.title,
                                  metadata_record={"origin": "manuscript", "unverified": True})
        old = db.get(m.BibliographicWork, work.id)
        work = old or work
        if not old:
            db.add(work)
            db.flush()
    ref.work_id = work.id
    return work


def save_source(db, run, ref, item, user_attached=False):
    ensure_work(db, run, ref)
    source_id = scoped_id(run, "source", f"{ref.id}:{item.get('id') or item.get('content_hash') or ref.id}")
    old = db.get(m.SourceDocument, source_id)
    if old:
        return old, []
    layout_warnings = []
    pages = item.get("pages", [])
    if (item.get("file_id") and pages and
            any(p.get("extraction_method") in {"PDF_NATIVE", "NATIVE", "OCR"} and
                (not isinstance(p.get("quality"), dict) or
                 p["quality"].get("prose_filter_version") != "paragraphs-v1") for p in pages)):
        extracted = call(get_settings().ocr_url, "/extract", {
            "file_id": str(item["file_id"]), "user_id": str(run.user_id),
            "document_version_id": str(run.document_version_id)},
            timeout=get_settings().stage_timeout)
        item = {**item, "pages": extracted["pages"]}
        layout_warnings = extracted.get("warnings", [])
    source = m.SourceDocument(
        id=source_id, run_id=run.id, work_id=ref.work_id, reference_id=ref.id,
        version=str(item.get("version", "1")), content_hash=item.get("content_hash") or
        hashlib.sha256(str(item.get("pages", [])).encode()).hexdigest(),
        file_id=uid(item["file_id"]) if item.get("file_id") else None,
        origin_url=item.get("origin_url"), final_url=item.get("final_url"),
        access_status=item.get("access_status", ref.access_status),
        identity_status=item.get("identity_status", ref.identity_status),
        user_attached=user_attached, details={**item, "user_id": str(run.user_id),
                                            "document_id": str(run.document_id)},
    )
    db.add(source)
    db.flush()
    warnings = save_chunks(db, run, item.get("pages", []), "SOURCE", source)
    return source, list(dict.fromkeys(layout_warnings + warnings))


def resolve_sources(db, run):
    if checkpoint(db, run.id, "MAPPING_CITATIONS") is None:
        raise HTTPException(409, "Todas as páginas devem ter resultado antes da consulta às fontes.")
    settings = get_settings()
    references = db.scalars(select(m.ReferenceEntry).join(m.OccurrenceReference,
        m.ReferenceEntry.id == m.OccurrenceReference.reference_id).where(
            m.ReferenceEntry.run_id == run.id, m.OccurrenceReference.relation == "RESOLVED").distinct()).all()
    warnings = []
    used_queries = sum(r.details.get("external_queries", 0) for r in rows(db, m.ReferenceEntry, run.id))
    for ref in references:
        ensure_active(db, run)
        if ref.source_resolved:
            continue
        work = ensure_work(db, run, ref)
        previous_id = run.config.get("copy_sources_from")
        copied = False
        if previous_id:
            previous = run_for(db, previous_id)
            if previous.document_id != run.document_id or previous.user_id != run.user_id:
                raise HTTPException(409, "Fonte complementar pertence a outro documento.")
            for old in rows(db, m.SourceDocument, previous.id):
                old_ref = db.get(m.ReferenceEntry, old.reference_id)
                if old.user_attached and old_ref.raw_text == ref.raw_text:
                    ref.identity_status, ref.access_status = old.identity_status, old.access_status
                    _, limits = save_source(db, run, ref, {**old.details, "file_id": str(old.file_id),
                        "identity_status": old.identity_status, "access_status": old.access_status}, True)
                    warnings += limits
                    copied = True
                    break
        if not copied:
            if used_queries >= settings.max_external_queries and run.config["mode"] != "demo":
                warnings.append("Limite de consultas externas atingido; referências restantes inconclusivas.")
                ref.identity_status, ref.access_status = "UNRESOLVED", "NOT_FOUND"
                ref.source_resolved = True
                db.commit()
                continue
            reserved = min(8, max(0, settings.max_external_queries - used_queries)) if run.config["mode"] == "real" else 0
            prior_queries = ref.details.get("external_queries", 0)
            ref.details = {**ref.details, "external_queries": prior_queries + reserved,
                           "query_reservation_unconfirmed": reserved}
            used_queries += reserved
            db.commit()  # Reserve before network I/O; a crash cannot reset the per-run budget.
            try:
                result = call(settings.web_search_url, "/resolve", {
                    "reference": {**record(ref), "work_id": str(work.id)}, "user_id": str(run.user_id),
                    "document_id": str(run.document_id), "mode": run.config["mode"],
                    "max_external_queries": reserved},
                    timeout=settings.stage_timeout)
            except Exception:
                result = {"identity_status": "UNRESOLVED", "access_status": "FETCH_ERROR",
                          "attempts": [], "external_queries": reserved,
                          "limitations": ["Falha técnica ao consultar a fonte; reserva de consultas mantida."]}
            ref.identity_status = result["identity_status"]
            ref.access_status = result["access_status"]
            work.metadata_record = {**work.metadata_record, "external": result.get("work", {}),
                                    "enrichment_origin": "web-search-service",
                                    "identity_status": ref.identity_status}
            for attempt in result.get("attempts", []):
                db.add(m.SearchAttempt(run_id=run.id, reference_id=ref.id,
                    provider=str(attempt.get("provider", "unknown"))[:80],
                    query=str(attempt.get("query", "")),
                    outcome=str(attempt.get("outcome", attempt.get("result", "recorded")))[:80], details=attempt))
            count = result.get("external_queries", 0)
            ref.details = {**ref.details, "external_queries": prior_queries + count,
                           "query_reservation_unconfirmed": 0,
                           "source_limitations": result.get("limitations", [])}
            used_queries += count - reserved
            warnings += result.get("limitations", [])
            if result.get("source"):
                _, limits = save_source(db, run, ref, {**result["source"],
                    "identity_status": ref.identity_status, "access_status": ref.access_status})
                warnings += limits
        ref.source_resolved = True
        db.commit()  # Each source is a bounded, recoverable checkpoint.
    return {"references_identified": sum(r.identity_status == "MATCHED" for r in references),
            "external_queries": used_queries, "warnings": list(dict.fromkeys(warnings))}


def pairs(db, run):
    result = []
    for link in db.scalars(select(m.ClaimOccurrence).where(m.ClaimOccurrence.run_id == run.id)):
        claim, occ = db.get(m.Claim, link.claim_id), db.get(m.CitationOccurrence, link.occurrence_id)
        refs = db.scalars(select(m.OccurrenceReference).where(
            m.OccurrenceReference.occurrence_id == occ.id, m.OccurrenceReference.relation == "RESOLVED")).all()
        for ref in refs or [None]:
            result.append((claim, occ, db.get(m.ReferenceEntry, ref.reference_id) if ref else None))
    return result


def evaluate(db, run):
    settings = get_settings()
    warnings = []
    total_tokens = db.scalar(select(func.coalesce(func.sum(m.CitationAssessment.tokens_used), 0)).where(
        m.CitationAssessment.run_id == run.id))
    debited_tokens = sum(c.details.get("token_budget_debited", 0) for c in rows(db, m.Claim, run.id))
    all_pairs = pairs(db, run)
    for claim, occurrence, ref in all_pairs:
        ensure_active(db, run)
        pair_key = f"{claim.id}:{occurrence.id}:{ref.id if ref else 'none'}"
        if db.scalar(select(m.CitationAssessment).where(m.CitationAssessment.pair_key == pair_key)):
            continue
        source = db.scalar(select(m.SourceDocument).where(
            m.SourceDocument.run_id == run.id, m.SourceDocument.reference_id == ref.id).order_by(
                m.SourceDocument.user_attached.desc(), m.SourceDocument.created_at.desc())) if ref else None
        evidence, scope, sufficient = [], "Nenhum conteúdo pertinente consultado.", False
        if source and ref.identity_status == "MATCHED":
            chunks = [record(c) for c in db.scalars(select(m.Chunk).where(m.Chunk.source_id == source.id))]
            try:
                rag = call(settings.rag_url, "/retrieve", {
                    "user_id": str(run.user_id), "document_id": str(run.document_id),
                    "work_id": str(source.work_id), "source_id": str(source.id), "version": source.version,
                    "claim": claim.text, "chunks": chunks},
                    timeout=2 * (settings.embedding_timeout + 45) + 15)
                evidence, scope = rag.get("evidence", []), rag.get("scope", "")
                sufficient = rag.get("coverage_sufficient", False)
                warnings += rag.get("limitations", [])
            except Exception:
                warnings.append("Recuperação de evidência indisponível para um dos pares.")
        stored_evidence, evidence_chunks = [], {}
        for item in evidence:
            # Provenance revalidated by the owner of persistent records, independently of the LLM.
            if (item.get("source_id") != str(source.id) or item.get("work_id") != str(source.work_id) or
                    str(item.get("version")) != source.version or item.get("origin") != "SOURCE" or
                    item.get("user_id") != str(run.user_id) or item.get("document_id") != str(run.document_id)):
                continue
            valid = db.get(m.Chunk, uid(item.get("id")))
            if not valid or valid.source_id != source.id or item["text"] != valid.text:
                continue
            eid = scoped_id(run, "evidence", item["id"])
            evidence_chunks[str(eid)] = item
            stored_evidence.append({**item, "id": str(eid)})
        token_budget = min(32000, max(0, settings.max_analysis_tokens - debited_tokens))
        payload = {
            "claim": record(claim), "occurrence": record(occurrence), "reference": record(ref) if ref else {},
            "source": {**source.details, **record(source), "user_id": str(run.user_id),
                       "document_id": str(run.document_id)} if source else None,
            "evidence": stored_evidence, "scope": scope, "coverage_sufficient": sufficient,
            "identity_status": source.identity_status if source else (ref.identity_status if ref else "UNRESOLVED"),
            "access_status": source.access_status if source else (ref.access_status if ref else "NOT_FOUND"),
            "mode": run.config["mode"],
            "max_tokens_budget": token_budget,
        }
        fitted, context_limits = fit_assessment_context(AssessmentRequest.model_validate(payload))
        payload = fitted.model_dump()
        stored_evidence, scope, sufficient = fitted.evidence, fitted.scope, fitted.coverage_sufficient
        warnings += context_limits
        for item in stored_evidence:
            eid = uid(item["id"])
            if not db.get(m.EvidencePassage, eid):
                db.add(m.EvidencePassage(id=eid, run_id=run.id, source_id=source.id,
                    work_id=source.work_id, version=source.version, text=item["text"],
                    page_start=item["page_start"], page_end=item["page_end"], details=evidence_chunks[item["id"]]))
        reservation = token_budget if run.config["mode"] == "real" else 0
        claim.details = {**claim.details, "token_budget_debited": claim.details.get("token_budget_debited", 0) + reservation}
        debited_tokens += reservation
        db.commit()  # Persist evidence and budget before requesting a paid model operation.
        answer = {}
        received_answer = False
        try:
            if not token_budget and run.config["mode"] == "real":
                raise ValueError("Token budget exhausted")
            answer = call(settings.llm_url, "/assess", payload, timeout=settings.request_timeout * 2)
            received_answer = True
            from packages.contracts.assessment import Assessment
            answer = Assessment.model_validate(answer).model_dump()
            # Verify exact quoted text again before persistent assessment/evidence relations are written.
            by_id = {e["id"]: e for e in stored_evidence}
            for ev in answer["evidence"]:
                if (ev["evidence_id"] not in by_id
                        or canonical_quote(ev["quote"]) not in canonical_quote(by_id[ev["evidence_id"]]["text"])):
                    raise ValueError("Unverifiable quote")
            if answer["semantic_status"] in {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED"}:
                if not answer["evidence"] or payload["identity_status"] != "MATCHED":
                    raise ValueError("Evidence required")
            if answer["semantic_status"] == "NOT_SUPPORTED_BY_REVIEWED_CONTENT" and not sufficient:
                raise ValueError("Coverage required")
        except Exception:
            reported_tokens = answer.get("tokens_used", 0)
            answer = {"semantic_status": "INCONCLUSIVE", "severity": "INFO",
                      "rationale": "Não foi possível obter um parecer validado para este vínculo.",
                      "evidence": [], "limitations": ["Modelo indisponível, limite atingido ou saída rejeitada."],
                      "reviewed_scope": scope_text(scope), "suggested_adjustment": "Revisar este vínculo manualmente.",
                      "tokens_used": reported_tokens if isinstance(reported_tokens, int) and reported_tokens >= 0 else 0}
        if received_answer:
            actual = answer["tokens_used"]
            claim.details = {**claim.details, "token_budget_debited":
                             claim.details.get("token_budget_debited", 0) - reservation + actual}
            debited_tokens += actual - reservation
        row = m.CitationAssessment(id=scoped_id(run, "assessment", pair_key), run_id=run.id,
            pair_key=pair_key, version=1, claim_id=claim.id, occurrence_id=occurrence.id,
            reference_id=ref.id if ref else None, binding_status=occurrence.binding_status,
            identity_status=payload["identity_status"], access_status=payload["access_status"],
            details={"prompt_version": answer.get("prompt_version"), "mode": run.config["mode"],
                     "group_note": "Parecer individual; apoio conjunto não é presumido."},
            **{k: answer[k] for k in ("semantic_status", "severity", "rationale",
                "suggested_adjustment", "reviewed_scope", "limitations", "tokens_used")})
        db.add(row)
        db.flush()
        for ev in answer["evidence"]:
            db.add(m.AssessmentEvidence(assessment_id=row.id, evidence_id=uid(ev["evidence_id"]),
                                        run_id=run.id, quote=ev["quote"]))
        total_tokens += row.tokens_used
        db.commit()
    return {"pairs_total": len(all_pairs), "pairs_evaluated": len(all_pairs),
            "tokens_used": total_tokens, "warnings": list(dict.fromkeys(warnings))}


HANDLERS = {"INGESTING": ingest, "EXTRACTING_REFERENCES": catalog,
            "MAPPING_CITATIONS": mapping, "RESOLVING_SOURCES": resolve_sources, "EVALUATING": evaluate}


def execute_stage(db, run, stage):
    old = checkpoint(db, run.id, stage)
    if old:
        return {k: v for k, v in old.result.items() if k != "catalog"}
    index = STAGES.index(stage)
    if index and checkpoint(db, run.id, STAGES[index - 1]) is None:
        raise HTTPException(409, "A etapa anterior ainda não foi persistida.")
    ensure_active(db, run)
    result = HANDLERS[stage](db, run)
    db.add(m.KnowledgeCheckpoint(run_id=run.id, stage=stage, result=result))
    db.commit()
    # Large extraction payloads stay in the database, not in broker events.
    return {k: v for k, v in result.items() if k != "catalog"}

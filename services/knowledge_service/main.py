import uuid
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, text
from packages.common import models as m
from packages.common.config import get_settings
from packages.common.db import get_db, readiness
from packages.common.http import call
from packages.common.serialization import record
from packages.common.web import create_app
from packages.contracts.api import CitationReview, FindingReview
from services.knowledge_service.pipeline import (
    HANDLERS, checkpoint, execute_stage, page_data, pairs, rows, run_for, save_source, uid,
)

app = create_app("knowledge-service")
app.state.readiness = readiness


def authorized_run(db, run_id, user_id):
    run = run_for(db, run_id)
    if run.user_id != uid(user_id):
        raise HTTPException(404, "Análise não encontrada.")
    return run


def citation_dict(db, obj):
    data = record(obj)
    links = db.scalars(select(m.OccurrenceReference).where(m.OccurrenceReference.occurrence_id == obj.id)).all()
    data["reference_ids"] = [str(x.reference_id) for x in links if x.relation == "RESOLVED"]
    data["candidate_reference_ids"] = [str(x.reference_id) for x in links if x.relation == "CANDIDATE"]
    data["reviews"] = [record(r) for r in db.scalars(select(m.ReviewAction).where(
        m.ReviewAction.occurrence_id == obj.id).order_by(m.ReviewAction.created_at))]
    return data


def finding_dict(db, obj):
    claim = db.get(m.Claim, obj.claim_id)
    occurrence = db.get(m.CitationOccurrence, obj.occurrence_id)
    ref = db.get(m.ReferenceEntry, obj.reference_id) if obj.reference_id else None
    return {**record(obj), "claim_text": claim.text, "marker": occurrence.marker,
            "page_start": occurrence.page_start, "page_end": occurrence.page_end,
            "reference_text": ref.raw_text if ref else None,
            "reviews": [record(r) for r in db.scalars(select(m.ReviewAction).where(
                m.ReviewAction.assessment_id == obj.id).order_by(m.ReviewAction.created_at))]}


def summary(db, run):
    refs, occs, findings = rows(db, m.ReferenceEntry, run.id), rows(db, m.CitationOccurrence, run.id), rows(db, m.CitationAssessment, run.id)
    coverage = rows(db, m.PageCoverage, run.id)
    total_pages = db.get(m.DocumentVersion, run.document_version_id).page_count
    return {
        "pages_total": total_pages, "pages_processed": len(coverage),
        "pages_readable": sum(p.status not in {"UNVERIFIABLE", "PARTIALLY_READABLE"} for p in coverage),
        "references_total": len(refs), "references_identified": sum(r.identity_status == "MATCHED" for r in refs),
        "occurrences_total": len(occs), "occurrences_resolved": sum(o.binding_status == "RESOLVED" for o in occs),
        "pairs_total": len(pairs(db, run)), "pairs_evaluated": len(findings),
        "inconclusive": sum(f.semantic_status in {"INCONCLUSIVE", "NOT_EVALUATED"} for f in findings),
        "tokens_used": sum(f.tokens_used for f in findings),
        "external_queries": sum(r.details.get("external_queries", 0) for r in refs),
    }


def snapshot(db, run):
    pages = page_data(db, run)
    coverage = {p.physical_page_number: p for p in rows(db, m.PageCoverage, run.id)}
    for page in pages:
        if page["physical_page_number"] in coverage:
            item = coverage[page["physical_page_number"]]
            page.update(status=item.status, error=item.error)
    linked = set(db.scalars(select(m.OccurrenceReference.reference_id).where(
        m.OccurrenceReference.run_id == run.id, m.OccurrenceReference.relation == "RESOLVED")))
    references = [{**record(r), "metadata": r.details, "has_detected_occurrence": r.id in linked}
                  for r in rows(db, m.ReferenceEntry, run.id)]
    findings = [finding_dict(db, f) for f in rows(db, m.CitationAssessment, run.id)]
    warnings = list(run.warnings)
    for check in rows(db, m.KnowledgeCheckpoint, run.id):
        warnings += check.result.get("warnings", [])
    if not coverage:
        warnings.append("O mapeamento não foi concluído; este snapshot é parcial.")
    return {
        "document": {**record(db.get(m.Document, run.document_id)),
                     "version": record(db.get(m.DocumentVersion, run.document_version_id))},
        "analysis": record(run), "run": record(run), "config": run.config, "summary": summary(db, run),
        "pages": pages, "references": references,
        "occurrences": [citation_dict(db, o) for o in rows(db, m.CitationOccurrence, run.id)],
        "claims": [record(c) for c in rows(db, m.Claim, run.id)],
        "assessments": findings, "findings": findings,
        "sources": [{**s.details, **record(s)} for s in rows(db, m.SourceDocument, run.id)],
        "evidence": [record(e) for e in rows(db, m.EvidencePassage, run.id)],
        "assessment_evidence": [record(e) for e in db.scalars(select(m.AssessmentEvidence).where(
            m.AssessmentEvidence.run_id == run.id))],
        "search_attempts": [record(a) for a in rows(db, m.SearchAttempt, run.id)],
        "review_actions": [record(a) for a in rows(db, m.ReviewAction, run.id)],
        "warnings": list(dict.fromkeys(str(w) for w in warnings)),
    }


@app.post("/runs/{run_id}/stages/{stage}")
def stage(run_id: uuid.UUID, stage: str, db=Depends(get_db)):
    if stage not in HANDLERS:
        raise HTTPException(404, "Etapa desconhecida.")
    # Dedicated session advisory lock survives source/assessment checkpoint commits.
    lock_key = int.from_bytes(run_id.bytes[:8], "big", signed=True)
    # A dedicated connection keeps the lock across per-source commits.
    from packages.common.db import engine
    with engine().connect() as lock_conn:
        if not lock_conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}):
            raise HTTPException(409, "Esta análise já está em processamento.")
        try:
            return execute_stage(db, run_for(db, run_id), stage)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            lock_conn.commit()


@app.get("/runs/{run_id}/summary")
def get_summary(run_id: uuid.UUID, user_id: uuid.UUID, db=Depends(get_db)):
    return summary(db, authorized_run(db, run_id, user_id))


@app.get("/runs/{run_id}/snapshot")
def get_snapshot(run_id: uuid.UUID, user_id: uuid.UUID, db=Depends(get_db)):
    return snapshot(db, authorized_run(db, run_id, user_id))


class SourceAttachment(BaseModel):
    user_id: uuid.UUID
    reference_id: uuid.UUID
    file_id: uuid.UUID
    version: str = Field("1", min_length=1, max_length=100)
    identity_confirmed: bool = False


@app.post("/runs/{run_id}/sources", status_code=201)
def attach_source(run_id: uuid.UUID, body: SourceAttachment, db=Depends(get_db)):
    run = authorized_run(db, run_id, body.user_id)
    ref = db.get(m.ReferenceEntry, body.reference_id)
    if not ref or ref.run_id != run.id:
        raise HTTPException(404, "Referência não encontrada nesta análise.")
    data = call(get_settings().ocr_url, "/extract", {"file_id": str(body.file_id),
        "user_id": str(body.user_id), "document_version_id": str(run.document_version_id)},
        timeout=get_settings().stage_timeout)
    # Attaching never mutates the original reference decision or earlier assessments.
    source, warnings = save_source(db, run, ref, {
        "id": str(body.file_id), "file_id": str(body.file_id), "version": body.version,
        "identity_status": "MATCHED" if body.identity_confirmed else "UNRESOLVED",
        "access_status": "PARTIAL_TEXT", "pages": data["pages"],
        "identity_basis": "human_confirmation" if body.identity_confirmed else "unverified_attachment",
        "limitations": ["Identidade declarada pelo usuário; edição e completude exigem revisão."],
    }, user_attached=True)
    db.commit()
    return {**record(source), "warnings": warnings, "requires_new_analysis": True}


class ReviewCitationCommand(CitationReview):
    user_id: uuid.UUID


@app.post("/citations/{occurrence_id}/review", status_code=201)
def review_citation(occurrence_id: uuid.UUID, body: ReviewCitationCommand, db=Depends(get_db)):
    occ = db.get(m.CitationOccurrence, occurrence_id)
    if not occ:
        raise HTTPException(404, "Ocorrência não encontrada.")
    run = authorized_run(db, occ.run_id, body.user_id)
    ref = db.get(m.ReferenceEntry, body.reference_id)
    if not ref or ref.run_id != run.id:
        raise HTTPException(422, "A referência deve pertencer à mesma análise.")
    action = m.ReviewAction(run_id=run.id, user_id=body.user_id, occurrence_id=occ.id,
        reference_id=ref.id, decision="RESOLVE_LINK", comment=body.comment, previous=citation_dict(db, occ))
    db.add(action)
    db.commit()
    return {**record(action), "requires_new_analysis": True}


class ReviewFindingCommand(FindingReview):
    user_id: uuid.UUID


@app.post("/findings/{finding_id}/review", status_code=201)
def review_finding(finding_id: uuid.UUID, body: ReviewFindingCommand, db=Depends(get_db)):
    finding = db.get(m.CitationAssessment, finding_id)
    if not finding:
        raise HTTPException(404, "Parecer não encontrado.")
    run = authorized_run(db, finding.run_id, body.user_id)
    action = m.ReviewAction(run_id=run.id, user_id=body.user_id, assessment_id=finding.id,
        decision=body.decision, comment=body.comment, previous=record(finding))
    db.add(action)
    db.commit()
    return record(action)


@app.post("/documents/{document_id}/reindex")
def reindex(document_id: uuid.UUID, user_id: uuid.UUID, db=Depends(get_db)):
    doc = db.get(m.Document, document_id)
    if not doc or doc.user_id != user_id:
        raise HTTPException(404, "Documento não encontrado.")
    # Explicit full deletion first avoids silently mixing embedding models.
    call(get_settings().vector_url, f"/documents/{document_id}?user_id={user_id}", method="DELETE")
    chunks = db.scalars(select(m.Chunk).where(m.Chunk.document_id == document_id, m.Chunk.user_id == user_id)).all()
    failures = []
    settings = get_settings()
    for start in range(0, len(chunks), settings.embedding_batch_size):
        batch = chunks[start:start + settings.embedding_batch_size]
        result = call(get_settings().vector_url, "/index", {
            "user_id": str(user_id), "document_id": str(document_id), "chunks": [record(c) for c in batch]},
            timeout=settings.embedding_timeout + 120)
        indexed = set(result.get("indexed_ids", []))
        for chunk in batch:
            chunk.index_state = "INDEXED" if str(chunk.id) in indexed else "PENDING"
            chunk.embedding = result.get("embedding", {})
            if chunk.index_state != "INDEXED":
                failures.append(str(chunk.id))
        db.commit()
    return {"total": len(chunks), "failed_ids": failures}

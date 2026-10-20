import re
import uuid
from pathlib import Path
from fastapi import Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from packages.common import models as m
from packages.common.config import get_settings
from packages.common.db import get_db, readiness
from packages.common.http import call, client
from packages.common.security import current_user, access_token, passwords, verify_password, rate_limit_auth
from packages.common.serialization import record
from packages.common.web import create_app
from packages.contracts.api import (
    Accepted, AnalysisOptions, CitationReview, Credentials, FindingReview,
    PageEnvelope, Registration, RunStatus, STAGES, TERMINAL,
)
from services.backend_api.events import enqueue_report, enqueue_stage
from services.knowledge_service.main import citation_dict, finding_dict, summary

app = create_app("backend-api", internal=False)
app.state.readiness = readiness
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","),
                   allow_credentials=False, allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
API = "/api/v1"


def own_document(db, document_id, user):
    doc = db.scalar(select(m.Document).where(m.Document.id == document_id, m.Document.user_id == user.id))
    if not doc:
        raise HTTPException(404, "Documento não encontrado.")
    if doc.deleting:
        raise HTTPException(409, "Exclusão do documento em andamento.")
    return doc


def own_run(db, run_id, user):
    run = db.scalar(select(m.AnalysisRun).where(m.AnalysisRun.id == run_id, m.AnalysisRun.user_id == user.id))
    if not run:
        raise HTTPException(404, "Análise não encontrada.")
    own_document(db, run.document_id, user)
    return run


def paginate(db, statement, limit, offset, transform=record):
    count = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    return {"items": [transform(o) for o in db.scalars(statement.limit(limit).offset(offset))],
            "total": count, "limit": limit, "offset": offset}


def token_result(user):
    return {"access_token": access_token(user), "token_type": "bearer",
            "user": {"id": str(user.id), "email": user.email, "name": user.name}}


@app.post(API + "/auth/register", status_code=201, tags=["auth"])
def register(body: Registration, request: Request, db=Depends(get_db)):
    rate_limit_auth(request)
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Informe um e-mail válido.")
    user = m.User(email=email, name=body.name.strip(), password_hash=passwords.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Não foi possível criar esta conta. Verifique o e-mail.")
    return token_result(user)


@app.post(API + "/auth/login", tags=["auth"])
def login(body: Credentials, request: Request, db=Depends(get_db)):
    rate_limit_auth(request)
    user = db.scalar(select(m.User).where(m.User.email == body.email.strip().lower()))
    if not user or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "E-mail ou senha inválidos.")
    return token_result(user)


@app.get(API + "/auth/me", tags=["auth"])
def me(user=Depends(current_user)):
    return {"id": str(user.id), "email": user.email, "name": user.name}


@app.post(API + "/documents", status_code=201, tags=["documents"])
async def upload_document(file: UploadFile = File(), user=Depends(current_user), db=Depends(get_db)):
    settings = get_settings()
    content = await file.read(settings.max_pdf_bytes + 1)
    if len(content) > settings.max_pdf_bytes:
        raise HTTPException(413, "Arquivo excede o limite de upload.")
    doc_id = uuid.uuid4()
    with client() as http:
        response = http.post(settings.upload_url + "/files",
            data={"user_id": str(user.id), "document_id": str(doc_id), "kind": "manuscript"},
            files={"file": (file.filename or "documento.pdf", content, file.content_type or "")})
        if response.status_code >= 400:
            detail = response.json()
            raise HTTPException(response.status_code, detail)
        stored = response.json()
    version_id = uuid.uuid4()
    doc = m.Document(id=doc_id, user_id=user.id, title=Path(file.filename or "Documento").name[:255],
                     current_version_id=version_id)
    db.add(doc)
    db.flush()
    db.add(m.DocumentVersion(id=version_id, document_id=doc_id, version=1,
        file_id=uuid.UUID(stored["file_id"]), content_hash=stored["content_hash"], page_count=stored["page_count"]))
    db.commit()
    return record(doc)


@app.get(API + "/documents", response_model=PageEnvelope, tags=["documents"])
def documents(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
              user=Depends(current_user), db=Depends(get_db)):
    return paginate(db, select(m.Document).where(m.Document.user_id == user.id)
                    .order_by(m.Document.created_at.desc()), limit, offset)


@app.get(API + "/documents/{document_id}", tags=["documents"])
def document(document_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    doc = own_document(db, document_id, user)
    return {**record(doc), "version": record(db.get(m.DocumentVersion, doc.current_version_id))}


def file_response(file_id, user_id, attachment=False):
    with client() as http:
        response = http.get(f"{get_settings().upload_url}/files/{file_id}", params={"user_id": str(user_id)})
        if response.status_code >= 400:
            raise HTTPException(response.status_code, "Arquivo indisponível.")
        if "application/pdf" not in response.headers.get("content-type", ""):
            raise HTTPException(415, "A fonte consultada não é um PDF. Consulte as passagens textuais.")
        return Response(response.content, media_type="application/pdf",
            headers={"Content-Disposition": f'{"attachment" if attachment else "inline"}; filename="documento.pdf"'})


@app.get(API + "/documents/{document_id}/file", tags=["documents"])
def document_file(document_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    doc = own_document(db, document_id, user)
    return file_response(db.get(m.DocumentVersion, doc.current_version_id).file_id, user.id)


@app.delete(API + "/documents/{document_id}", status_code=202, tags=["documents"])
def delete_document(document_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    doc = db.scalar(select(m.Document).where(m.Document.id == document_id, m.Document.user_id == user.id).with_for_update())
    if not doc:
        raise HTTPException(404, "Documento não encontrado.")
    active = db.scalar(select(m.AnalysisRun).where(m.AnalysisRun.document_id == doc.id,
                       m.AnalysisRun.status.not_in(list(TERMINAL))))
    if active:
        raise HTTPException(409, "Cancele a análise em andamento e aguarde sua interrupção antes de excluir.")
    pending_report = db.scalar(select(m.Report).join(m.AnalysisRun, m.Report.run_id == m.AnalysisRun.id).where(
        m.AnalysisRun.document_id == doc.id, m.Report.status.in_(["QUEUED", "GENERATING"])))
    if pending_report:
        raise HTTPException(409, "Aguarde a geração dos relatórios pendentes antes de excluir.")
    if not doc.deleting:
        doc.deleting = True
        db.add(m.OutboxEvent(event_key=f"delete:{doc.id}", task_name="citalens.delete", queue="maintenance",
            payload={"document_id": str(doc.id), "user_id": str(user.id), "event_version": 1}))
        db.commit()
    elif doc.deletion_error:
        event = db.scalar(select(m.OutboxEvent).where(m.OutboxEvent.event_key == f"delete:{doc.id}"))
        event.attempts, event.published_at, event.delivery_state = 0, None, "PENDING"
        event.available_at, event.last_error, doc.deletion_error = m.now(), None, None
        db.commit()
    return {"id": str(doc.id), "status": "DELETING"}


@app.post(API + "/documents/{document_id}/analyses", status_code=202,
          response_model=Accepted, tags=["analyses"])
def start_analysis(document_id: uuid.UUID, options: AnalysisOptions, user=Depends(current_user), db=Depends(get_db)):
    doc = own_document(db, document_id, user)
    # Serializes new runs against deletion and duplicate concurrent requests.
    db.refresh(doc, with_for_update=True)
    if options.mode == "demo" and not get_settings().demo_enabled:
        raise HTTPException(403, "Demonstração desabilitada.")
    if options.bibliography_page_end and options.bibliography_page_end > db.get(
            m.DocumentVersion, doc.current_version_id).page_count:
        raise HTTPException(422, "O intervalo da bibliografia excede o documento.")
    active = db.scalar(select(m.AnalysisRun).where(m.AnalysisRun.document_id == doc.id,
                       m.AnalysisRun.status.not_in(list(TERMINAL))))
    if active:
        return {"id": active.id, "status": active.status, "progress_url": f"{API}/analyses/{active.id}/progress"}
    config = options.model_dump(mode="json")
    previous = db.scalar(select(m.AnalysisRun).where(m.AnalysisRun.document_id == doc.id).order_by(
        m.AnalysisRun.created_at.desc()))
    if not config["copy_sources_from"] and previous:
        config["copy_sources_from"] = str(previous.id)
    if config["copy_sources_from"]:
        source_run = own_run(db, uuid.UUID(config["copy_sources_from"]), user)
        if source_run.document_id != doc.id:
            raise HTTPException(422, "Histórico deve pertencer ao mesmo documento.")
    run = m.AnalysisRun(id=uuid.uuid4(), document_id=doc.id, document_version_id=doc.current_version_id,
                        user_id=user.id, config=config, status=RunStatus.QUEUED)
    db.add(run)
    db.flush()
    for name in STAGES:
        db.add(m.AnalysisStep(run_id=run.id, name=name))
    enqueue_stage(db, run, STAGES[0])
    db.commit()
    return {"id": run.id, "status": run.status, "progress_url": f"{API}/analyses/{run.id}/progress"}


@app.get(API + "/documents/{document_id}/analyses", response_model=PageEnvelope, tags=["analyses"])
def document_analyses(document_id: uuid.UUID, limit: int = Query(50, ge=1, le=100),
                      offset: int = Query(0, ge=0), user=Depends(current_user), db=Depends(get_db)):
    own_document(db, document_id, user)
    return paginate(db, select(m.AnalysisRun).where(m.AnalysisRun.document_id == document_id).order_by(
        m.AnalysisRun.created_at.desc()), limit, offset)


@app.get(API + "/analyses/{run_id}", tags=["analyses"])
def analysis(run_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    run = own_run(db, run_id, user)
    return {**record(run), "summary": summary(db, run),
            "reports": [record(r) for r in db.scalars(select(m.Report).where(m.Report.run_id == run.id).order_by(
                m.Report.version.desc()))]}


@app.get(API + "/analyses/{run_id}/progress", tags=["analyses"])
def progress(run_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    run = own_run(db, run_id, user)
    steps = db.scalars(select(m.AnalysisStep).where(m.AnalysisStep.run_id == run.id)).all()
    return {"status": run.status, "stage": run.status, **summary(db, run),
            "cancel_requested": run.cancel_requested,
            "steps": sorted([record(s) for s in steps], key=lambda s: STAGES.index(s["name"]))}


@app.post(API + "/analyses/{run_id}/cancel", status_code=202, tags=["analyses"])
def cancel(run_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    run = own_run(db, run_id, user)
    if run.status not in TERMINAL:
        run.cancel_requested = True
        db.commit()
    return {"id": str(run.id), "status": run.status, "cancel_requested": run.cancel_requested}


@app.get(API + "/analyses/{run_id}/references", response_model=PageEnvelope, tags=["review"])
def references(run_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               user=Depends(current_user), db=Depends(get_db)):
    own_run(db, run_id, user)
    linked = set(db.scalars(select(m.OccurrenceReference.reference_id).where(
        m.OccurrenceReference.run_id == run_id, m.OccurrenceReference.relation == "RESOLVED")))
    return paginate(db, select(m.ReferenceEntry).where(m.ReferenceEntry.run_id == run_id).order_by(
        m.ReferenceEntry.page_start, m.ReferenceEntry.id), limit, offset,
        lambda r: {**record(r), "has_detected_occurrence": r.id in linked})


@app.get(API + "/analyses/{run_id}/pages", response_model=PageEnvelope, tags=["review"])
def pages(run_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user=Depends(current_user), db=Depends(get_db)):
    run = own_run(db, run_id, user)
    coverage = {p.physical_page_number: p for p in db.scalars(select(m.PageCoverage).where(m.PageCoverage.run_id == run.id))}
    def page_result(p):
        result = record(p)
        if p.physical_page_number in coverage:
            status = coverage[p.physical_page_number]
            result.update(status=status.status, error=status.error)
        else:
            result["status"] = "PENDING"
        return result
    return paginate(db, select(m.Page).where(m.Page.document_version_id == run.document_version_id).order_by(
        m.Page.physical_page_number), limit, offset, page_result)


@app.get(API + "/analyses/{run_id}/citations", response_model=PageEnvelope, tags=["review"])
def citations(run_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
              page: int | None = Query(None, ge=1), status: str | None = None,
              user=Depends(current_user), db=Depends(get_db)):
    own_run(db, run_id, user)
    query = select(m.CitationOccurrence).where(m.CitationOccurrence.run_id == run_id)
    if page:
        query = query.where(m.CitationOccurrence.page_start <= page, m.CitationOccurrence.page_end >= page)
    if status:
        query = query.where(m.CitationOccurrence.binding_status == status)
    return paginate(db, query.order_by(m.CitationOccurrence.page_start, m.CitationOccurrence.offset_start),
                    limit, offset, lambda o: citation_dict(db, o))


@app.get(API + "/analyses/{run_id}/findings", response_model=PageEnvelope, tags=["review"])
def findings(run_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
             page: int | None = Query(None, ge=1), reference_id: uuid.UUID | None = None,
             severity: str | None = None, status: str | None = None,
             user=Depends(current_user), db=Depends(get_db)):
    own_run(db, run_id, user)
    query = select(m.CitationAssessment).join(m.CitationOccurrence,
        m.CitationAssessment.occurrence_id == m.CitationOccurrence.id).where(m.CitationAssessment.run_id == run_id)
    if page:
        query = query.where(m.CitationOccurrence.page_start <= page, m.CitationOccurrence.page_end >= page)
    if reference_id:
        query = query.where(m.CitationAssessment.reference_id == reference_id)
    if severity:
        query = query.where(m.CitationAssessment.severity == severity)
    if status:
        query = query.where(m.CitationAssessment.semantic_status == status)
    return paginate(db, query.order_by(m.CitationOccurrence.page_start, m.CitationAssessment.id),
                    limit, offset, lambda f: finding_dict(db, f))


@app.get(API + "/findings/{finding_id}/evidence", tags=["review"])
def evidence(finding_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    finding = db.get(m.CitationAssessment, finding_id)
    if not finding:
        raise HTTPException(404, "Parecer não encontrado.")
    own_run(db, finding.run_id, user)
    items = []
    for link in db.scalars(select(m.AssessmentEvidence).where(m.AssessmentEvidence.assessment_id == finding.id)):
        passage = db.get(m.EvidencePassage, link.evidence_id)
        source = db.get(m.SourceDocument, passage.source_id)
        items.append({**record(passage), "quote": link.quote, "origin_url": source.origin_url,
                      "final_url": source.final_url, "accessed_at": source.accessed_at,
                      "source_version": source.version, "source_file_available": source.file_id is not None})
    return {"items": items, "total": len(items)}


@app.post(API + "/citations/{occurrence_id}/review", status_code=201, tags=["review"])
def review_citation(occurrence_id: uuid.UUID, body: CitationReview, user=Depends(current_user), db=Depends(get_db)):
    occurrence = db.get(m.CitationOccurrence, occurrence_id)
    if not occurrence:
        raise HTTPException(404, "Ocorrência não encontrada.")
    own_run(db, occurrence.run_id, user)
    return call(get_settings().knowledge_url, f"/citations/{occurrence_id}/review",
                {**body.model_dump(mode="json"), "user_id": str(user.id)})


@app.post(API + "/findings/{finding_id}/review", status_code=201, tags=["review"])
def review_finding(finding_id: uuid.UUID, body: FindingReview, user=Depends(current_user), db=Depends(get_db)):
    finding = db.get(m.CitationAssessment, finding_id)
    if not finding:
        raise HTTPException(404, "Parecer não encontrado.")
    own_run(db, finding.run_id, user)
    return call(get_settings().knowledge_url, f"/findings/{finding_id}/review",
                {**body.model_dump(mode="json"), "user_id": str(user.id)})


@app.post(API + "/analyses/{run_id}/sources", status_code=201, tags=["sources"])
async def attach_source(run_id: uuid.UUID, file: UploadFile = File(), reference_id: uuid.UUID = Form(),
                        version: str = Form("1"), identity_confirmed: bool = Form(False),
                        user=Depends(current_user), db=Depends(get_db)):
    run = own_run(db, run_id, user)
    doc = db.scalar(select(m.Document).where(m.Document.id == run.document_id).with_for_update(key_share=True))
    db.refresh(doc)
    if doc.deleting:
        raise HTTPException(409, "Exclusão do documento em andamento.")
    ref = db.get(m.ReferenceEntry, reference_id)
    if not ref or ref.run_id != run.id:
        raise HTTPException(404, "Referência não encontrada.")
    if run.status not in TERMINAL:
        raise HTTPException(409, "Aguarde ou cancele a análise antes de anexar uma fonte.")
    data = await file.read(get_settings().max_source_bytes + 1)
    if len(data) > get_settings().max_source_bytes:
        raise HTTPException(413, "Fonte excede o limite de upload.")
    with client() as http:
        response = http.post(get_settings().upload_url + "/files",
            data={"user_id": str(user.id), "document_id": str(run.document_id), "kind": "source"},
            files={"file": (file.filename or "fonte.pdf", data, file.content_type or "")})
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.json())
        stored = response.json()
    result = call(get_settings().knowledge_url, f"/runs/{run.id}/sources", {
        "user_id": str(user.id), "reference_id": str(reference_id), "file_id": stored["file_id"],
        "version": version, "identity_confirmed": identity_confirmed}, timeout=get_settings().stage_timeout)
    db.commit()
    return result


@app.get(API + "/sources/{source_id}/file", tags=["sources"])
def source_file(source_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    source = db.get(m.SourceDocument, source_id)
    if not source:
        raise HTTPException(404, "Fonte não encontrada.")
    own_run(db, source.run_id, user)
    if not source.file_id:
        raise HTTPException(404, "A fonte não possui arquivo PDF armazenado.")
    return file_response(source.file_id, user.id)


@app.post(API + "/analyses/{run_id}/reports", status_code=202, response_model=Accepted, tags=["reports"])
def create_report(run_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    run = own_run(db, run_id, user)
    doc = db.scalar(select(m.Document).where(m.Document.id == run.document_id).with_for_update(key_share=True))
    db.refresh(doc)
    if doc.deleting:
        raise HTTPException(409, "Exclusão do documento em andamento.")
    db.refresh(run, with_for_update=True)
    if run.status not in TERMINAL:
        raise HTTPException(409, "Aguarde a conclusão ou o cancelamento para gerar um relatório parcial.")
    version = db.scalar(select(func.coalesce(func.max(m.Report.version), 0)).where(m.Report.run_id == run.id)) + 1
    report = m.Report(id=uuid.uuid4(), run_id=run.id, version=version)
    db.add(report)
    db.flush()
    enqueue_report(db, report)
    db.commit()
    return {"id": report.id, "status": report.status, "progress_url": f"{API}/analyses/{run.id}"}


@app.get(API + "/reports/{report_id}/download", tags=["reports"])
def download_report(report_id: uuid.UUID, user=Depends(current_user), db=Depends(get_db)):
    report = db.get(m.Report, report_id)
    if not report:
        raise HTTPException(404, "Relatório não encontrado.")
    own_run(db, report.run_id, user)
    if report.status != "COMPLETED" or report.file_id is None:
        raise HTTPException(409, "Relatório ainda não está pronto.")
    return file_response(report.file_id, user.id, attachment=True)


@app.get(API + "/demo/manuscript", tags=["demo"])
def demo_manuscript(user=Depends(current_user)):
    if not get_settings().demo_enabled:
        raise HTTPException(404, "Demonstração desabilitada.")
    path = Path("tests/fixtures/demo-manuscript.pdf")
    if not path.exists():
        path = Path("tests/fixtures/textual.pdf")
    return Response(path.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="demonstracao-sintetica.pdf"'})

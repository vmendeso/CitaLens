"""At-least-once delivery: persistent stages, bounded retries and transactional outbox."""
import hashlib
import uuid
from datetime import timedelta
from celery import Celery
from sqlalchemy import select, text
from packages.common import models as m
from packages.common.config import get_settings
from packages.common.db import engine, transaction
from packages.common.http import call, client
from packages.contracts.api import STAGES, TERMINAL, RunStatus
from services.backend_api.events import enqueue_stage

settings = get_settings()
celery_app = Celery("citalens", broker=settings.broker_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    broker_transport_options={"confirm_publish": True},
    task_soft_time_limit=settings.stage_timeout + 30, task_time_limit=settings.stage_timeout + 60,
    task_default_queue="analysis", timezone="UTC",
)


def mark_processed(db, event_id):
    key = uuid.UUID(event_id)
    if not db.get(m.ProcessedEvent, key):
        db.add(m.ProcessedEvent(event_id=key))


def render_report(report_id):
    # This lock serializes report deliveries and excludes document deletion/attachment.
    # NO KEY UPDATE still permits FK checks from the storage/knowledge services.
    with engine().begin() as lock_conn:
        document = lock_conn.execute(
            select(m.Document.id, m.Document.deleting)
            .join(m.AnalysisRun, m.AnalysisRun.document_id == m.Document.id)
            .join(m.Report, m.Report.run_id == m.AnalysisRun.id)
            .where(m.Report.id == report_id)
            .with_for_update(of=m.Document, key_share=True)
        ).first()
        if not document or document.deleting:
            raise RuntimeError("Document unavailable for report")
        return _render_report_locked(report_id)


def _render_report_locked(report_id):
    with transaction() as db:
        report = db.get(m.Report, report_id)
        if report.status == "COMPLETED":
            return
        run = db.get(m.AnalysisRun, report.run_id)
        run_id, user_id, document_id, version = run.id, run.user_id, run.document_id, report.version
        if not report.snapshot:
            snapshot = call(settings.knowledge_url, f"/runs/{run.id}/snapshot?user_id={run.user_id}", method="GET")
            snapshot["report"] = {"id": str(report.id), "version": report.version,
                                  "created_at": m.now().isoformat()}
            report.snapshot = snapshot
        else:
            snapshot = report.snapshot
        report.status = "GENERATING"
    with client(settings.stage_timeout) as http:
        rendered = http.post(settings.report_url + "/render", json={"snapshot": snapshot})
        rendered.raise_for_status()
        uploaded = http.post(settings.upload_url + "/files",
            data={"user_id": str(user_id), "document_id": str(document_id), "kind": "report",
                  "idempotency_key": str(report_id)},
            files={"file": (f"relatorio-v{version}.pdf", rendered.content, "application/pdf")})
        uploaded.raise_for_status()
        artifact = uploaded.json()
    with transaction() as db:
        report = db.get(m.Report, report_id)
        report.file_id, report.content_hash = uuid.UUID(artifact["file_id"]), artifact["content_hash"]
        report.status = "COMPLETED"


@celery_app.task(bind=True, name="citalens.stage", max_retries=3)
def process_stage(self, event_id, run_id, stage, event_version=1):
    run_uuid = uuid.UUID(run_id)
    lock_key = int.from_bytes(hashlib.sha256(f"worker:{run_id}".encode()).digest()[:8], "big", signed=True)
    with engine().connect() as lock_conn:
        if not lock_conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}):
            # Another delivery owns the run; the outbox watchdog will recover it if needed.
            return
        try:
            with transaction() as db:
                if db.get(m.ProcessedEvent, uuid.UUID(event_id)):
                    return
                run = db.get(m.AnalysisRun, run_uuid)
                if not run:
                    return
                if run.cancel_requested:
                    run.status, run.finished_at = RunStatus.CANCELLED, m.now()
                    mark_processed(db, event_id)
                    return
                if run.status in TERMINAL:
                    mark_processed(db, event_id)
                    return
                step = db.scalar(select(m.AnalysisStep).where(m.AnalysisStep.run_id == run.id, m.AnalysisStep.name == stage))
                previous = STAGES.index(stage) - 1
                if previous >= 0:
                    prerequisite = db.scalar(select(m.AnalysisStep).where(
                        m.AnalysisStep.run_id == run.id, m.AnalysisStep.name == STAGES[previous]))
                    if prerequisite.status != "COMPLETED":
                        raise RuntimeError("Prerequisite not committed")
                if run.started_at and (m.now() - run.started_at).total_seconds() > settings.max_analysis_seconds:
                    run.status, run.error, run.finished_at = RunStatus.FAILED, "Limite de duração atingido.", m.now()
                    mark_processed(db, event_id)
                    return
                if step.attempts >= 4:
                    run.status, run.error, run.finished_at = RunStatus.FAILED, "Tentativas da etapa esgotadas.", m.now()
                    mark_processed(db, event_id)
                    return
                run.status, run.started_at = RunStatus(stage), run.started_at or m.now()
                step.status, step.attempts, step.started_at = "RUNNING", step.attempts + 1, step.started_at or m.now()
            if stage == "GENERATING_REPORT":
                with transaction() as db:
                    report = db.scalar(select(m.Report).where(m.Report.run_id == run_uuid, m.Report.version == 1))
                    if not report:
                        report = m.Report(id=uuid.uuid5(run_uuid, "initial-report"), run_id=run_uuid, version=1)
                        db.add(report)
                    report_id = report.id
                render_report(report_id)
                result = {}
            else:
                result = call(settings.knowledge_url, f"/runs/{run_id}/stages/{stage}",
                              {"event_version": event_version}, timeout=settings.stage_timeout)
            with transaction() as db:
                run = db.get(m.AnalysisRun, run_uuid)
                step = db.scalar(select(m.AnalysisStep).where(m.AnalysisStep.run_id == run.id, m.AnalysisStep.name == stage))
                step.status, step.result, step.finished_at, step.error = "COMPLETED", result, m.now(), None
                run.warnings = list(dict.fromkeys(run.warnings + result.get("warnings", [])))
                run.tokens_used = max(run.tokens_used, result.get("tokens_used", 0))
                run.external_queries = max(run.external_queries, result.get("external_queries", 0))
                mark_processed(db, event_id)
                if run.cancel_requested:
                    run.status, run.finished_at = RunStatus.CANCELLED, m.now()
                elif stage == STAGES[-1]:
                    run.summary = call(settings.knowledge_url,
                        f"/runs/{run.id}/summary?user_id={run.user_id}", method="GET")
                    has_limits = run.warnings or run.summary.get("inconclusive") or (
                        run.summary.get("pages_readable", 0) != run.summary.get("pages_total", 0))
                    run.status = RunStatus.COMPLETED_WITH_WARNINGS if has_limits else RunStatus.COMPLETED
                    run.finished_at = m.now()
                else:
                    enqueue_stage(db, run, STAGES[STAGES.index(stage) + 1])
        except Exception as exc:
            with transaction() as db:
                run = db.get(m.AnalysisRun, run_uuid)
                if run:
                    step = db.scalar(select(m.AnalysisStep).where(
                        m.AnalysisStep.run_id == run.id, m.AnalysisStep.name == stage))
                    if run.cancel_requested:
                        run.status, run.finished_at = RunStatus.CANCELLED, m.now()
                        mark_processed(db, event_id)
                        return
                    step.error = "Falha operacional na etapa; consulta externa ou serviço indisponível."
                    step.status = "RETRYING"
                    if step.attempts >= 4 or self.request.retries >= 3:
                        step.status = "FAILED"
                        run.status, run.error, run.finished_at = RunStatus.FAILED, step.error, m.now()
                        mark_processed(db, event_id)
                        return
            raise self.retry(exc=RuntimeError("Stage temporarily unavailable"), countdown=10 * 2**self.request.retries)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            lock_conn.commit()


@celery_app.task(bind=True, name="citalens.report", max_retries=3)
def report_task(self, event_id, report_id, event_version=1):
    report_uuid = uuid.UUID(report_id)
    with transaction() as db:
        if db.get(m.ProcessedEvent, uuid.UUID(event_id)):
            return
    try:
        render_report(report_uuid)
        with transaction() as db:
            mark_processed(db, event_id)
    except Exception:
        if self.request.retries >= 3:
            with transaction() as db:
                db.get(m.Report, report_uuid).status = "FAILED"
                mark_processed(db, event_id)
            return
        raise self.retry(countdown=10 * 2**self.request.retries)


@celery_app.task(bind=True, name="citalens.delete", max_retries=3)
def delete_task(self, event_id, document_id, user_id, event_version=1):
    with transaction() as db:
        if db.get(m.ProcessedEvent, uuid.UUID(event_id)):
            return
        document = db.get(m.Document, uuid.UUID(document_id))
        if not document:
            mark_processed(db, event_id)
            return
        if str(document.user_id) != user_id or not document.deleting:
            raise ValueError("Deletion scope mismatch")
    try:
        call(settings.vector_url, f"/documents/{document_id}?user_id={user_id}", method="DELETE")
        call(settings.upload_url, f"/documents/{document_id}?user_id={user_id}", method="DELETE")
        with transaction() as db:
            document = db.get(m.Document, uuid.UUID(document_id))
            if document:
                db.delete(document)  # Cascades relational artifacts after external deletion succeeds.
            mark_processed(db, event_id)
    except Exception:
        if self.request.retries >= 3:
            # Keep deleting=true and unprocessed event so the bounded watchdog retries later.
            return
        raise self.retry(countdown=10 * 2**self.request.retries)

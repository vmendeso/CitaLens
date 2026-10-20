"""One dispatcher publishes durable events; replays stale unprocessed deliveries."""
import logging
import time
import uuid
from pathlib import Path
from datetime import timedelta
from sqlalchemy import select, or_
from packages.common import models as m
from packages.common.db import transaction
from services.backend_api.worker import celery_app


def dispatch_once():
    with transaction() as db:
        event = db.scalar(select(m.OutboxEvent).outerjoin(
            m.ProcessedEvent, m.ProcessedEvent.event_id == m.OutboxEvent.id
        ).where(
            m.ProcessedEvent.event_id.is_(None),
            or_(m.OutboxEvent.published_at.is_(None), m.OutboxEvent.published_at < m.now() - timedelta(minutes=5)),
            m.OutboxEvent.delivery_state != "DEAD_LETTER",
            m.OutboxEvent.available_at <= m.now(),
        ).order_by(m.OutboxEvent.created_at).with_for_update(skip_locked=True, of=m.OutboxEvent).limit(1))
        if not event:
            return False
        if event.attempts >= 20:
            event.delivery_state = "DEAD_LETTER"
            event.last_error = "Limite de entrega atingido; verifique os serviços e solicite nova execução."
            if event.run_id:
                run = db.get(m.AnalysisRun, event.run_id)
                if run and run.status not in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "CANCELLED", "FAILED"}:
                    run.status, run.error, run.finished_at = "FAILED", event.last_error, m.now()
            if event.payload.get("report_id"):
                report = db.get(m.Report, uuid.UUID(event.payload["report_id"]))
                if report and report.status != "COMPLETED":
                    report.status = "FAILED"
            if event.task_name == "citalens.delete":
                doc = db.get(m.Document, uuid.UUID(event.payload["document_id"]))
                if doc:
                    doc.deletion_error = "Exclusão incompleta. Repita a solicitação após restabelecer os serviços."
            return True
        event.attempts += 1
        try:
            celery_app.send_task(event.task_name, kwargs={"event_id": str(event.id), **event.payload},
                                 queue=event.queue, task_id=str(event.id))
        except Exception:
            event.last_error = "Broker indisponível ou confirmação de publicação ausente."
            event.available_at = m.now() + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
            return True
        # Broker confirms precede this commit. A crash here causes a safe duplicate.
        event.published_at = m.now()
        event.delivery_state = "PUBLISHED"
        event.last_error = None
        event.available_at = m.now() + timedelta(minutes=5)
        return True


def main():
    while True:
        try:
            Path("/tmp/citalens-outbox-heartbeat").touch()
            if not dispatch_once():
                time.sleep(2)
        except Exception:
            logging.getLogger("citalens").error("outbox_dispatch_failed; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()

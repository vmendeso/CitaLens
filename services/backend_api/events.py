import uuid
from packages.common.models import OutboxEvent

QUEUES = {"INGESTING": "ingest", "EXTRACTING_REFERENCES": "analysis",
          "MAPPING_CITATIONS": "analysis", "RESOLVING_SOURCES": "sources",
          "EVALUATING": "evaluation", "GENERATING_REPORT": "reports"}


def enqueue_stage(db, run, stage):
    key = f"{run.id}:{stage}:1"
    event = OutboxEvent(id=uuid.uuid5(run.id, key), run_id=run.id, event_key=key,
        task_name="citalens.stage", queue=QUEUES[stage],
        payload={"run_id": str(run.id), "stage": stage, "event_version": 1})
    db.add(event)
    return event


def enqueue_report(db, report):
    event = OutboxEvent(run_id=report.run_id, event_key=f"report:{report.id}",
        task_name="citalens.report", queue="reports",
        payload={"report_id": str(report.id), "event_version": 1})
    db.add(event)
    return event

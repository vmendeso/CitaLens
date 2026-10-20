"""Opt-in real PostgreSQL migrations, constraints, domain roles and queued workflow.

Set RUN_INTEGRATION=1, INTEGRATION_ADMIN_DATABASE_URL and INTEGRATION_API_URL
against the local Compose stack. The migration fixture creates and removes only
a generated citalens_test_<uuid> database; it never resets the application DB.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from alembic import command
from alembic.config import Config
import httpx
import pymupdf
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.common import models as m
from packages.contracts.api import STAGES

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1",
                       reason="RUN_INTEGRATION=1 exige PostgreSQL, fila e APIs reais do Compose"),
]


@pytest.fixture(scope="module")
def admin_url():
    value = os.getenv("INTEGRATION_ADMIN_DATABASE_URL")
    if not value:
        pytest.skip("Defina INTEGRATION_ADMIN_DATABASE_URL para uma instância PostgreSQL de testes.")
    return make_url(value)


@pytest.fixture(scope="module")
def migrated_database(admin_url):
    test_name = "citalens_test_" + uuid.uuid4().hex
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    target_url = admin_url.set(database=test_name).render_as_string(hide_password=False)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{test_name}"'))
    configuration = Config("alembic.ini")
    configuration.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))
    previous_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = target_url
    target = create_engine(target_url)
    try:
        command.upgrade(configuration, "head")
        yield target
        command.downgrade(configuration, "base")
        with target.connect() as connection:
            assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name='knowledge')"))
    finally:
        if previous_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_migration_url
        target.dispose()
        # The identifier is generated above and is never taken from user input.
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'))
        admin.dispose()


def test_postgres_empty_upgrade_contains_real_constraints_and_scoped_roles(migrated_database):
    inspector = inspect(migrated_database)
    assert {"pages", "reference_entries", "citation_occurrences", "claims", "citation_assessments",
            "assessment_evidence", "source_documents"} <= set(inspector.get_table_names(schema="knowledge"))
    assert {"documents", "document_versions", "analysis_runs", "outbox_events", "processed_events"} <= set(
        inspector.get_table_names(schema="orchestration"))
    unique = inspector.get_unique_constraints("pages", schema="knowledge")
    assert any(set(c["column_names"]) == {"document_version_id", "physical_page_number"} for c in unique)
    for table in m.Base.metadata.tables.values():
        actual_columns = {column["name"] for column in inspector.get_columns(table.name, schema=table.schema)}
        assert actual_columns == set(table.columns.keys()), f"Migração e ORM divergem em {table.fullname}"
    with migrated_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        assert connection.scalar(text("SELECT has_table_privilege('api_writer', 'orchestration.analysis_runs', 'INSERT')"))
        assert not connection.scalar(text("SELECT has_table_privilege('api_writer', 'knowledge.reference_entries', 'INSERT')"))
        assert connection.scalar(text("SELECT has_table_privilege('knowledge_writer', 'knowledge.reference_entries', 'INSERT')"))
        assert not connection.scalar(text("SELECT has_table_privilege('knowledge_writer', 'orchestration.documents', 'DELETE')"))
        assert connection.scalar(text("SELECT has_table_privilege('upload_writer', 'storage.files', 'INSERT')"))
        assert not connection.scalar(text("SELECT has_table_privilege('upload_writer', 'knowledge.reference_entries', 'SELECT')"))


def test_postgres_rejects_cross_user_analysis_and_duplicate_page_and_event(migrated_database):
    with Session(migrated_database, expire_on_commit=False) as db:
        alice = m.User(email=f"alice-{uuid.uuid4()}@test.invalid", name="Alice", password_hash="fixture")
        bob = m.User(email=f"bob-{uuid.uuid4()}@test.invalid", name="Bob", password_hash="fixture")
        db.add_all([alice, bob])
        db.flush()
        document = m.Document(user_id=alice.id, title="Documento sintético")
        db.add(document)
        db.flush()
        version = m.DocumentVersion(document_id=document.id, version=1, file_id=uuid.uuid4(),
                                    content_hash="a" * 64, page_count=1)
        db.add(version)
        db.flush()
        valid_run = m.AnalysisRun(document_id=document.id, document_version_id=version.id, user_id=alice.id, config={})
        db.add(valid_run)
        db.commit()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(m.AnalysisRun(document_id=document.id, document_version_id=version.id, user_id=bob.id, config={}))
                db.flush()
        first = m.Page(document_version_id=version.id, physical_index=0, physical_page_number=1,
                       extraction_method="NATIVE", status="EXTRACTED", quality={"characters": 5})
        db.add(first)
        db.commit()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(m.Page(document_version_id=version.id, physical_index=0, physical_page_number=1,
                              extraction_method="NATIVE", status="EXTRACTED"))
                db.flush()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(m.Page(document_version_id=version.id, physical_index=-1, physical_page_number=0,
                              extraction_method="NATIVE", status="EXTRACTED"))
                db.flush()
        key = f"integration:{valid_run.id}"
        db.add(m.OutboxEvent(run_id=valid_run.id, event_key=key, task_name="fixture", queue="fixture", payload={}))
        db.commit()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(m.OutboxEvent(run_id=valid_run.id, event_key=key, task_name="fixture", queue="fixture", payload={}))
                db.flush()


def _register(http, email):
    response = http.post("/api/v1/auth/register", json={"name": "Teste de integração",
                         "email": email, "password": "integration-fixture-password-2026"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_real_queued_demo_workflow_download_isolation_and_checkpoints(admin_url):
    base_url = os.getenv("INTEGRATION_API_URL", "http://localhost:8000")
    live_db = create_engine(admin_url)
    document_id = None
    with httpx.Client(base_url=base_url, timeout=60) as http:
        headers = _register(http, f"integration-{uuid.uuid4()}@example.invalid")
        stranger = _register(http, f"integration-other-{uuid.uuid4()}@example.invalid")
        try:
            fixture = Path("tests/fixtures/demo-manuscript.pdf").read_bytes()
            upload = http.post("/api/v1/documents", headers=headers,
                               files={"file": ("demo-manuscript.pdf", fixture, "application/pdf")})
            assert upload.status_code == 201, upload.text
            document_id = upload.json()["id"]
            accepted = http.post(f"/api/v1/documents/{document_id}/analyses", headers=headers, json={"mode": "demo"})
            assert accepted.status_code == 202, accepted.text
            run_id = accepted.json()["id"]
            deadline = time.monotonic() + int(os.getenv("INTEGRATION_TIMEOUT", "180"))
            while time.monotonic() < deadline:
                status = http.get(f"/api/v1/analyses/{run_id}", headers=headers)
                assert status.status_code == 200, status.text
                analysis = status.json()
                if analysis["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}:
                    break
                time.sleep(.5)
            else:
                pytest.fail(f"Análise {run_id} não terminou dentro do timeout.")
            assert analysis["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}, analysis
            summary = analysis["summary"]
            assert summary["pages_total"] == 3 and summary["pages_processed"] == 3
            assert summary["references_total"] == 1 and summary["occurrences_total"] == 3
            findings = http.get(f"/api/v1/analyses/{run_id}/findings", headers=headers).json()["items"]
            assert {finding["semantic_status"] for finding in findings} == {
                "SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED"}
            for finding in findings:
                response = http.get(f"/api/v1/findings/{finding['id']}/evidence", headers=headers)
                assert response.status_code == 200 and response.json()["total"] > 0
                assert http.get(f"/api/v1/findings/{finding['id']}/evidence", headers=stranger).status_code == 404
            pages = http.get(f"/api/v1/analyses/{run_id}/pages", headers=headers).json()
            assert pages["total"] == 3
            assert all(p["status"] in {"ANALYZED", "NO_CITATIONS", "BIBLIOGRAPHIC"} for p in pages["items"])
            report = next(r for r in analysis["reports"] if r["status"] == "COMPLETED")
            downloaded = http.get(f"/api/v1/reports/{report['id']}/download", headers=headers)
            assert downloaded.status_code == 200 and downloaded.headers["content-type"].startswith("application/pdf")
            with pymupdf.open(stream=downloaded.content, filetype="pdf") as pdf:
                assert "DEMONSTRAÇÃO SINTÉTICA" in "\n".join(p.get_text() for p in pdf)
            assert http.get(f"/api/v1/reports/{report['id']}/download", headers=stranger).status_code == 404
            assert http.get(f"/api/v1/documents/{document_id}/file", headers=stranger).status_code == 404
            with live_db.connect() as connection:
                values = {"run_id": run_id}
                steps = connection.execute(text(
                    "SELECT name, status, started_at, finished_at FROM orchestration.analysis_steps WHERE run_id=:run_id"), values).mappings().all()
                by_stage = {step["name"]: step for step in steps}
                assert set(by_stage) == set(STAGES)
                assert all(step["status"] == "COMPLETED" for step in steps)
                assert by_stage["EXTRACTING_REFERENCES"]["finished_at"] <= by_stage["MAPPING_CITATIONS"]["started_at"]
                assert by_stage["MAPPING_CITATIONS"]["finished_at"] <= by_stage["RESOLVING_SOURCES"]["started_at"]
                assert connection.scalar(text(
                    "SELECT count(*) FROM orchestration.outbox_events e JOIN orchestration.processed_events p ON p.event_id=e.id WHERE e.run_id=:run_id"), values) >= len(STAGES)
                assert connection.scalar(text(
                    "SELECT count(*) FROM knowledge.checkpoints WHERE run_id=:run_id"), values) == len(STAGES) - 1
                assert connection.scalar(text(
                    "SELECT count(*) FROM knowledge.chunks WHERE document_id=:document_id"), {"document_id": document_id}) > 0
        finally:
            if document_id:
                # User-authorized API deletion; the actual removal is asynchronous.
                http.delete(f"/api/v1/documents/{document_id}", headers=headers)
    live_db.dispose()


def test_inprocess_persisted_demo_pipeline_and_replay(migrated_database, monkeypatch, tmp_path):
    """Real SQL and service logic; HTTP is in-process, broker is explicitly drained.

    This validates recovery checkpoints and graceful vector unavailability, not
    RabbitMQ delivery or Weaviate indexing (covered by separate opt-in tests).
    """
    import importlib
    from fastapi.testclient import TestClient
    from packages.common.config import get_settings
    from packages.common.db import engine

    monkeypatch.setenv("DATABASE_URL", migrated_database.url.render_as_string(hide_password=False))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "none")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("WEAVIATE_HOST", "127.0.0.1")
    get_settings.cache_clear()
    engine.cache_clear()
    settings = get_settings()
    modules = {}
    clients = {}
    for folder in ("backend_api", "upload_service", "ocr_service", "chunk_service",
                   "knowledge_service", "vector_service", "rag_service", "llm_service",
                   "web_search_service", "report_service"):
        module = importlib.import_module(f"services.{folder}.main")
        module.app.dependency_overrides.clear()
        modules[folder] = module
        for cached in ("provider", "store", "resolver"):
            if hasattr(module, cached):
                getattr(module, cached).cache_clear()
        host = "backend-api" if folder == "backend_api" else folder.replace("_", "-")
        clients[host] = TestClient(module.app)

    class RoutedClient:
        def __init__(self, *args, **kwargs):
            self.headers = kwargs.get("headers", {})

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            pass

        def request(self, method, url, **kwargs):
            parsed = urlsplit(str(url))
            if parsed.hostname not in clients:
                raise httpx.ConnectError("Serviço externo indisponível neste teste local.")
            headers = {**self.headers, **kwargs.pop("headers", {})}
            kwargs.pop("timeout", None)
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            return clients[parsed.hostname].request(method, path, headers=headers, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def delete(self, url, **kwargs):
            return self.request("DELETE", url, **kwargs)

        @contextmanager
        def stream(self, method, url, **kwargs):
            yield self.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "Client", RoutedClient)
    from services.backend_api import worker
    monkeypatch.setattr(worker, "settings", settings)
    http = clients["backend-api"]
    try:
        headers = _register(http, f"inprocess-{uuid.uuid4()}@example.invalid")
        stranger = _register(http, f"inprocess-other-{uuid.uuid4()}@example.invalid")
        uploaded = http.post("/api/v1/documents", headers=headers, files={"file": (
            "demo-manuscript.pdf", Path("tests/fixtures/demo-manuscript.pdf").read_bytes(), "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["id"]
        response = http.post(f"/api/v1/documents/{document_id}/analyses", headers=headers, json={"mode": "demo"})
        assert response.status_code == 202, response.text
        run_id = uuid.UUID(response.json()["id"])
        delivered = []
        for _ in range(len(STAGES) + 2):
            with Session(migrated_database) as db:
                event = db.scalar(select(m.OutboxEvent).outerjoin(
                    m.ProcessedEvent, m.ProcessedEvent.event_id == m.OutboxEvent.id
                ).where(m.OutboxEvent.run_id == run_id, m.ProcessedEvent.event_id.is_(None)).order_by(m.OutboxEvent.created_at))
                if event is None:
                    break
                payload = {"event_id": str(event.id), **event.payload}
            worker.process_stage.run(**payload)
            delivered.append(payload)
        assert len(delivered) == len(STAGES)
        response = http.get(f"/api/v1/analyses/{run_id}", headers=headers)
        assert response.status_code == 200, response.text
        analysis = response.json()
        assert analysis["status"] == "COMPLETED_WITH_WARNINGS", analysis
        assert analysis["summary"]["pages_total"] == 3
        assert analysis["summary"]["occurrences_total"] == 3
        findings_response = http.get(f"/api/v1/analyses/{run_id}/findings", headers=headers)
        assert findings_response.status_code == 200, findings_response.text
        findings = findings_response.json()["items"]
        assert {finding["semantic_status"] for finding in findings} == {
            "SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED"}, findings
        report = next(r for r in analysis["reports"] if r["status"] == "COMPLETED")
        pdf_response = http.get(f"/api/v1/reports/{report['id']}/download", headers=headers)
        assert pdf_response.status_code == 200, pdf_response.text
        with pymupdf.open(stream=pdf_response.content, filetype="pdf") as pdf:
            report_text = "\n".join(p.get_text() for p in pdf)
            assert "DEMONSTRAÇÃO SINTÉTICA" in report_text
            assert "aumento de 12%" in report_text
        assert http.get(f"/api/v1/reports/{report['id']}/download", headers=stranger).status_code == 404
        with Session(migrated_database) as db:
            counts_before = {
                model: len(db.scalars(select(model).where(model.run_id == run_id)).all())
                for model in (m.CitationOccurrence, m.Claim, m.CitationAssessment, m.EvidencePassage)
            }
            from services.knowledge_service.pipeline import execute_stage
            run = db.get(m.AnalysisRun, run_id)
            execute_stage(db, run, "MAPPING_CITATIONS")
        # Simulate at-least-once redelivery after the checkpoint, not a fresh run.
        for payload in delivered:
            worker.process_stage.run(**payload)
        with Session(migrated_database) as db:
            counts_after = {
                model: len(db.scalars(select(model).where(model.run_id == run_id)).all())
                for model in counts_before
            }
            assert counts_before == counts_after
            stored_report = db.get(m.Report, uuid.UUID(report["id"]))
            snapshot = stored_report.snapshot
            assert len(snapshot["assessment_evidence"]) == 3
            assert db.scalar(select(m.KnowledgeCheckpoint).where(
                m.KnowledgeCheckpoint.run_id == run_id, m.KnowledgeCheckpoint.stage == "EXTRACTING_REFERENCES"))
        # A repeat renderer invocation uses identical bytes and the same upload key.
        worker.render_report(uuid.UUID(report["id"]))
        replay_download = http.get(f"/api/v1/reports/{report['id']}/download", headers=headers)
        assert replay_download.content == pdf_response.content
        # Cooperative cancellation still permits an explicitly partial snapshot.
        pending = http.post(f"/api/v1/documents/{document_id}/analyses", headers=headers, json={"mode": "demo"})
        assert pending.status_code == 202, pending.text
        cancelled_id = uuid.UUID(pending.json()["id"])
        assert http.post(f"/api/v1/analyses/{cancelled_id}/cancel", headers=headers).status_code == 202
        with Session(migrated_database) as db:
            event = db.scalar(select(m.OutboxEvent).where(m.OutboxEvent.run_id == cancelled_id))
            cancel_payload = {"event_id": str(event.id), **event.payload}
        worker.process_stage.run(**cancel_payload)
        cancelled = http.get(f"/api/v1/analyses/{cancelled_id}", headers=headers).json()
        assert cancelled["status"] == "CANCELLED"
        partial = http.post(f"/api/v1/analyses/{cancelled_id}/reports", headers=headers)
        assert partial.status_code == 202, partial.text
        partial_id = partial.json()["id"]
        # A pending renderer must finish before files can be deleted.
        blocked_delete = http.delete(f"/api/v1/documents/{document_id}", headers=headers)
        assert blocked_delete.status_code == 409, blocked_delete.text
        with Session(migrated_database) as db:
            event = db.scalar(select(m.OutboxEvent).where(
                m.OutboxEvent.run_id == cancelled_id, m.OutboxEvent.task_name == "citalens.report"))
            report_payload = {"event_id": str(event.id), **event.payload}
        worker.report_task.run(**report_payload)
        partial_pdf = http.get(f"/api/v1/reports/{partial_id}/download", headers=headers)
        assert partial_pdf.status_code == 200, partial_pdf.text
        with pymupdf.open(stream=partial_pdf.content, filetype="pdf") as pdf:
            assert "snapshot é parcial" in "\n".join(p.get_text() for p in pdf)
    finally:
        for client in clients.values():
            client.close()
        engine().dispose()
        engine.cache_clear()
        get_settings.cache_clear()
        for module in modules.values():
            for cached in ("provider", "store", "resolver"):
                if hasattr(module, cached):
                    getattr(module, cached).cache_clear()


def test_outbox_exhaustion_is_visible_and_failed_publish_does_not_claim_success(migrated_database, monkeypatch):
    """Broker failure is simulated; outbox transitions and commits use real PostgreSQL."""
    from datetime import timedelta
    from services.backend_api import outbox

    @contextmanager
    def local_transaction():
        with Session(migrated_database, expire_on_commit=False) as db:
            with db.begin():
                yield db

    monkeypatch.setattr(outbox, "transaction", local_transaction)
    with local_transaction() as db:
        # Isolate dispatcher selection from records created by preceding tests in this disposable DB.
        for event in db.scalars(select(m.OutboxEvent)):
            if not db.get(m.ProcessedEvent, event.id):
                db.add(m.ProcessedEvent(event_id=event.id))
        user = m.User(email=f"outbox-{uuid.uuid4()}@example.invalid", name="Teste", password_hash="fixture")
        db.add(user)
        db.flush()
        doc = m.Document(user_id=user.id, title="Outbox")
        db.add(doc)
        db.flush()
        version = m.DocumentVersion(document_id=doc.id, version=1, file_id=uuid.uuid4(),
                                    content_hash="0" * 64, page_count=1)
        db.add(version)
        db.flush()
        run = m.AnalysisRun(document_id=doc.id, document_version_id=version.id, user_id=user.id)
        db.add(run)
        db.flush()
        exhausted = m.OutboxEvent(run_id=run.id, event_key=f"exhausted:{run.id}", task_name="citalens.stage",
            queue="analysis", payload={}, attempts=20, available_at=m.now() - timedelta(minutes=10))
        db.add(exhausted)
        db.flush()
        event_id, run_id = exhausted.id, run.id
    assert outbox.dispatch_once()
    with local_transaction() as db:
        assert db.get(m.OutboxEvent, event_id).delivery_state == "DEAD_LETTER"
        assert db.get(m.AnalysisRun, run_id).status == "FAILED"
        failed = m.OutboxEvent(event_key=f"unavailable:{uuid.uuid4()}", task_name="citalens.stage",
                              queue="analysis", payload={})
        db.add(failed)
        db.flush()
        failed_id = failed.id

    def unavailable(*args, **kwargs):
        raise ConnectionError("Broker test failure")

    monkeypatch.setattr(outbox.celery_app, "send_task", unavailable)
    assert outbox.dispatch_once()
    with local_transaction() as db:
        item = db.get(m.OutboxEvent, failed_id)
        assert item.attempts == 1
        assert item.published_at is None
        assert item.last_error
        assert item.available_at > item.created_at

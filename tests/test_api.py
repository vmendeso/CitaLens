"""HTTP contract and authorization checks; no live database or provider calls."""
from datetime import timedelta
import importlib
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy.orm import configure_mappers

from packages.common import models as m
from packages.common.config import get_settings
from packages.common.db import get_db
from packages.common.security import access_token
from services.backend_api.main import app as backend
from services.upload_service.main import app as upload
from services.upload_service.storage import LocalStorage


class ObjectDatabase:
    """Evaluate SQL equality filters against fixed objects, including owner filters.

    This is a unit double. PostgreSQL constraints and row privileges have separate
    real integration tests; it deliberately does not claim to simulate a database.
    """
    def __init__(self, objects):
        self.objects = {(type(obj), obj.id): obj for obj in objects}
        self.queries = []

    def get(self, model, identifier):
        return self.objects.get((model, identifier))

    def scalar(self, statement):
        self.queries.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        parameters = statement.compile().params
        candidates = [obj for (model, _), obj in self.objects.items() if model is entity]
        for name, value in parameters.items():
            attribute = name.rsplit("_", 1)[0]
            if hasattr(entity, attribute):
                candidates = [obj for obj in candidates if getattr(obj, attribute) == value]
        return candidates[0] if candidates else None


@pytest.fixture
def accounts():
    alice = m.User(id=uuid.uuid4(), email="alice@example.test", name="Alice", password_hash="unused")
    bob = m.User(id=uuid.uuid4(), email="bob@example.test", name="Bob", password_hash="unused")
    document = m.Document(id=uuid.uuid4(), user_id=bob.id, title="Documento de Bob", deleting=False)
    version = m.DocumentVersion(id=uuid.uuid4(), document_id=document.id, version=1,
                                file_id=uuid.uuid4(), content_hash="a" * 64, page_count=1)
    document.current_version_id = version.id
    run = m.AnalysisRun(id=uuid.uuid4(), user_id=bob.id, document_id=document.id,
                        document_version_id=version.id, status="COMPLETED", config={})
    finding = m.CitationAssessment(id=uuid.uuid4(), run_id=run.id)
    source = m.SourceDocument(id=uuid.uuid4(), run_id=run.id, file_id=uuid.uuid4())
    report = m.Report(id=uuid.uuid4(), run_id=run.id, status="COMPLETED", file_id=uuid.uuid4())
    stored = m.StoredFile(id=uuid.uuid4(), user_id=bob.id, document_id=document.id,
                          object_key="bob/document.pdf")
    database = ObjectDatabase([alice, bob, document, version, run, finding, source, report, stored])
    backend.dependency_overrides[get_db] = lambda: database
    upload.dependency_overrides[get_db] = lambda: database
    yield {"alice": alice, "bob": bob, "document": document, "run": run, "finding": finding,
           "source": source, "report": report, "stored": stored, "database": database}
    backend.dependency_overrides.clear()
    upload.dependency_overrides.clear()


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    value = response.json()
    assert {"code", "message", "correlation_id"} <= value.keys()
    uuid.UUID(value["correlation_id"])
    if code:
        assert value["code"] == code
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    return value


@pytest.mark.parametrize("module", [
    ".".join(path.with_suffix("").parts) for path in Path("services").glob("*/main.py")
])
def test_all_service_imports_and_openapi_are_valid(module):
    application = importlib.import_module(module).app
    schema = application.openapi()
    assert schema["openapi"].startswith("3.")
    assert "/health/live" in schema["paths"] and "/health/ready" in schema["paths"]
    configure_mappers()


def test_public_api_requires_valid_token_and_rejects_tampering(accounts):
    with TestClient(backend) as http:
        assert_error(http.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
        assert_error(http.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}), 401, "AUTH_REQUIRED")
        token = access_token(accounts["alice"])
        response = http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["id"] == str(accounts["alice"].id)
        expired = jwt.encode({"sub": str(accounts["alice"].id), "iat": m.now() - timedelta(hours=2),
                              "exp": m.now() - timedelta(hours=1), "iss": "citalens", "aud": "citalens-api"},
                             get_settings().jwt_secret, algorithm="HS256")
        assert_error(http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}), 401, "AUTH_REQUIRED")


@pytest.mark.parametrize("object_name,route", [
    ("document", "/api/v1/documents/{id}"),
    ("document", "/api/v1/documents/{id}/file"),
    ("run", "/api/v1/analyses/{id}/progress"),
    ("run", "/api/v1/analyses/{id}/references"),
    ("finding", "/api/v1/findings/{id}/evidence"),
    ("source", "/api/v1/sources/{id}/file"),
    ("report", "/api/v1/reports/{id}/download"),
])
def test_cross_user_objects_are_hidden(accounts, object_name, route):
    with TestClient(backend) as http:
        response = http.get(route.format(id=accounts[object_name].id),
                            headers={"Authorization": f"Bearer {access_token(accounts['alice'])}"})
    assert_error(response, 404)


def test_owner_can_read_document_and_structured_validation_is_safe(accounts):
    headers = {"Authorization": f"Bearer {access_token(accounts['bob'])}"}
    with TestClient(backend) as http:
        response = http.get(f"/api/v1/documents/{accounts['document'].id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["title"] == "Documento de Bob"
        error = assert_error(http.get("/api/v1/documents/not-a-uuid", headers=headers), 422, "VALIDATION_ERROR")
        assert error["details"]
        assert "password" not in str(error).lower()


def test_internal_token_is_required_for_internal_openapi_and_operations():
    from services.chunk_service.main import app
    with TestClient(app) as http:
        assert http.get("/health/live").status_code == 200
        assert_error(http.post("/chunk", json={}), 401, "INTERNAL_AUTH_REQUIRED")
        assert_error(http.get("/openapi.json"), 401, "INTERNAL_AUTH_REQUIRED")
        response = http.post("/chunk", json={}, headers={"X-Internal-Token": get_settings().internal_token})
        assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("filename,content,mime,expected", [
    ("manuscript.exe", b"%PDF-1.7\nbad", "application/pdf", "INVALID_EXTENSION"),
    ("manuscript.pdf", b"not a PDF", "application/pdf", "INVALID_PDF_SIGNATURE"),
    ("manuscript.pdf", b"%PDF-1.7\nbad", "image/png", "INVALID_CONTENT_TYPE"),
    ("manuscript.pdf", b"%PDF-1.7\nbad", "application/pdf", "CORRUPT_PDF"),
])
def test_upload_rejects_invalid_pdf_before_storage(accounts, filename, content, mime, expected):
    with TestClient(upload) as http:
        response = http.post("/files", headers={"X-Internal-Token": get_settings().internal_token},
                             data={"user_id": str(accounts["alice"].id), "document_id": str(uuid.uuid4())},
                             files={"file": (filename, content, mime)})
    assert_error(response, 422, expected)


def test_storage_download_checks_owner_before_reading_disk(accounts, monkeypatch):
    def forbidden_read(*args, **kwargs):
        pytest.fail("O arquivo de outro usuário não deve ser lido.")
    monkeypatch.setattr(LocalStorage, "get", forbidden_read)
    with TestClient(upload) as http:
        response = http.get(f"/files/{accounts['stored'].id}",
                            params={"user_id": str(accounts["alice"].id)},
                            headers={"X-Internal-Token": get_settings().internal_token})
    assert_error(response, 404)


def test_storage_traversal_is_rejected_and_atomic_roundtrip_works(tmp_path):
    storage = LocalStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="Invalid object key"):
        storage.get("../outside.pdf")
    storage.put("user/doc/file.pdf", b"original")
    storage.put("user/doc/file.pdf", b"replacement")
    assert storage.get("user/doc/file.pdf") == b"replacement"
    assert not list((tmp_path / "objects").rglob("*.tmp"))

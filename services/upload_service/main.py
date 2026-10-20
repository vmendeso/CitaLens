import hashlib
import uuid
from pathlib import Path
from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from packages.common.config import get_settings
from packages.common.db import get_db, readiness
from packages.common.models import StoredFile
from packages.common.serialization import record
from packages.common.web import create_app
from packages.document_processing.pdf import validate_pdf, PDFValidationError
from services.upload_service.storage import LocalStorage

app = create_app("upload-service")
app.state.readiness = readiness


async def store_upload(file, user_id, document_id, kind, idempotency_key, db, artifact=False):
    settings = get_settings()
    max_bytes = settings.max_source_bytes if kind == "source" else settings.max_pdf_bytes
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, "Arquivo excede o limite configurado.")
    if kind not in {"source", "manuscript", "report"}:
        raise HTTPException(422, "Tipo de arquivo inválido.")
    filename = Path(file.filename or "documento.pdf").name[:255]
    if artifact:
        if kind != "source" or file.content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
            raise HTTPException(415, "Tipo de artefato não permitido.")
        if not data:
            raise HTTPException(422, "Arquivo vazio.")
        page_count = 1
    else:
        try:
            result = validate_pdf(data, filename, file.content_type or "",
                                  max_bytes=max_bytes, max_pages=settings.max_pdf_pages)
            page_count = result["page_count"] if isinstance(result, dict) else int(result)
        except PDFValidationError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc)})
    content_hash = hashlib.sha256(data).hexdigest()
    file_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{user_id}/{document_id}/{kind}/{idempotency_key or content_hash}")
    existing = db.get(StoredFile, file_id)
    if existing:
        if existing.content_hash != content_hash:
            raise HTTPException(409, "A chave de idempotência já identifica outro conteúdo.")
        return {**record(existing), "file_id": str(existing.id)}
    key = f"{user_id}/{document_id}/{file_id}.{'artifact' if artifact else 'pdf'}"
    LocalStorage(settings.storage_root).put(key, data)
    row = StoredFile(id=file_id, user_id=user_id, document_id=document_id, object_key=key,
                     filename=filename, content_hash=content_hash, size_bytes=len(data),
                     page_count=page_count, kind=kind)
    db.add(row)
    db.commit()
    return {**record(row), "file_id": str(row.id)}


@app.post("/files", status_code=201)
async def upload(file: UploadFile = File(), user_id: uuid.UUID = Form(),
                 document_id: uuid.UUID = Form(), kind: str = Form("manuscript"),
                 idempotency_key: str | None = Form(None), db=Depends(get_db)):
    return await store_upload(file, user_id, document_id, kind, idempotency_key, db)


@app.post("/artifacts", status_code=201)
async def artifact(file: UploadFile = File(), user_id: uuid.UUID = Form(),
                   document_id: uuid.UUID = Form(), kind: str = Form("source"),
                   idempotency_key: str | None = Form(None), db=Depends(get_db)):
    return await store_upload(file, user_id, document_id, kind, idempotency_key, db, artifact=True)


@app.get("/files/{file_id}")
def download(file_id: uuid.UUID, user_id: uuid.UUID, db=Depends(get_db)):
    row = db.scalar(select(StoredFile).where(StoredFile.id == file_id, StoredFile.user_id == user_id))
    if row is None:
        raise HTTPException(404, "Arquivo não encontrado.")
    try:
        content = LocalStorage(get_settings().storage_root).get(row.object_key)
    except FileNotFoundError:
        raise HTTPException(503, "Artefato ausente do armazenamento. Restaure o backup.")
    media = "application/pdf" if row.object_key.endswith(".pdf") else "application/octet-stream"
    return Response(content, media_type=media, headers={"Content-Disposition": 'inline; filename="documento.pdf"'})


@app.delete("/documents/{document_id}")
def delete_document(document_id: uuid.UUID, user_id: uuid.UUID, db=Depends(get_db)):
    rows = db.scalars(select(StoredFile).where(
        StoredFile.document_id == document_id, StoredFile.user_id == user_id)).all()
    storage = LocalStorage(get_settings().storage_root)
    for row in rows:
        storage.delete(row.object_key)
        db.delete(row)
    db.commit()
    return {"deleted": len(rows)}

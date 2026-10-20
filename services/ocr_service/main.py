from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from packages.common.config import get_settings
from packages.common.web import create_app
from packages.document_processing.pdf import PDFValidationError, extract_pdf, validate_pdf

app = create_app("ocr-service", internal=True)


class ExtractRequest(BaseModel):
    file_id: UUID
    user_id: UUID
    document_version_id: UUID


@app.post("/extract")
def extract(request: ExtractRequest):
    settings = get_settings()
    # Stream under a bound: internal storage is not trusted to send a safe size.
    payload = bytearray()
    with httpx.Client(timeout=settings.request_timeout, follow_redirects=False) as client:
        with client.stream("GET", f"{settings.upload_url}/files/{request.file_id}",
                           params={"user_id": str(request.user_id)},
                           headers={"X-Internal-Token": settings.internal_token}) as response:
            response.raise_for_status()
            for piece in response.iter_bytes(64 * 1024):
                payload.extend(piece)
                if len(payload) > settings.max_pdf_bytes:
                    raise HTTPException(413, detail={"code": "FILE_SIZE_LIMIT", "message": "PDF excede o limite permitido."})
    try:
        validate_pdf(bytes(payload), "document.pdf", "application/pdf", settings.max_pdf_bytes, settings.max_pdf_pages)
        return extract_pdf(bytes(payload), languages=settings.ocr_languages, max_pages=settings.max_pdf_pages,
                           max_seconds=max(1, settings.stage_timeout - 10))
    except PDFValidationError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc

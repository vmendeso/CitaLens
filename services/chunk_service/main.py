from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

from packages.common.web import create_app
from packages.document_processing.chunks import chunk_pages

app = create_app("chunk-service", internal=True)


class ChunkRequest(BaseModel):
    pages: list[dict] = Field(max_length=2000)
    origin: Literal["MANUSCRIPT", "SOURCE"]
    document_id: UUID
    document_version_id: UUID
    user_id: UUID
    work_id: UUID | None = None
    source_id: UUID | None = None
    version: str = Field(default="1", max_length=100)


@app.post("/chunk")
def chunk(request: ChunkRequest):
    try:
        return chunk_pages(**request.model_dump(mode="json"))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail={"code": "INVALID_CHUNK_INPUT", "message": str(exc)}) from exc

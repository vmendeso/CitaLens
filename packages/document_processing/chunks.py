"""One prose paragraph per chunk, with exact page-local provenance."""
import hashlib
import uuid

from packages.document_processing.prose import paragraph_spans


def chunk_pages(pages: list[dict], origin: str, document_id: str,
                document_version_id: str, user_id: str, work_id: str | None = None,
                source_id: str | None = None, version: str = "1",
                size: int = 1800, overlap: int = 250) -> dict:
    if origin not in {"MANUSCRIPT", "SOURCE"}:
        raise ValueError("origin deve ser MANUSCRIPT ou SOURCE.")
    if origin == "SOURCE" and (not work_id or not source_id):
        raise ValueError("Chunks de fonte exigem work_id e source_id.")
    # Legacy arguments remain accepted for callers of v1. Paragraph boundaries
    # alone determine chunks: even a long paragraph is never cut or overlapped.
    chunks = []
    for page in sorted(pages, key=lambda p: p["physical_page_number"]):
        text = page.get("normalized_text", "")
        for span in paragraph_spans(page):
            start, end = span["offset_start"], span["offset_end"]
            fragment = text[start:end]
            if fragment.strip():
                digest = hashlib.sha256(fragment.encode()).hexdigest()
                key = "|".join(map(str, (user_id, document_id, document_version_id, origin,
                                        work_id, source_id, version, page["physical_page_number"], start, end, digest)))
                chunks.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)), "text": fragment,
                               "origin": origin, "document_id": document_id,
                               "document_version_id": document_version_id, "user_id": user_id,
                               "work_id": work_id, "source_id": source_id, "version": version,
                               "page_start": page["physical_page_number"],
                               "page_end": page["physical_page_number"],
                               "offset_start": start, "offset_end": end,
                               "content_hash": digest, "ordinal": len(chunks)})
    return {"chunks": chunks}

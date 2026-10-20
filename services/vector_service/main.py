from functools import lru_cache

from fastapi import HTTPException, Query

from packages.common.config import get_settings
from packages.common.web import create_app
from packages.contracts.assessment import IndexRequest, SearchRequest
from packages.providers.vector import VectorStore

app = create_app("vector-service")


@lru_cache
def store():
    return VectorStore(get_settings())


def readiness():
    try:
        store().ready()
    except Exception as exc:
        raise HTTPException(503, "Weaviate indisponível.") from exc


app.state.readiness = readiness


@app.post("/index")
def index(request: IndexRequest):
    try:
        return store().index(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/search")
def search(request: SearchRequest):
    try:
        return store().search(request)
    except Exception as exc:
        raise HTTPException(503, "Recuperação vetorial indisponível; use os chunks persistidos.") from exc


@app.delete("/documents/{document_id}")
def delete(document_id: str, user_id: str = Query(min_length=1)):
    return store().delete_document(document_id, user_id)

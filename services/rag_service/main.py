from fastapi import HTTPException

from packages.common.config import get_settings
from packages.common.web import create_app
from packages.contracts.assessment import RetrieveRequest
from packages.providers.retrieval import retrieve

app = create_app("rag-service")


@app.post("/retrieve")
def retrieve_evidence(request: RetrieveRequest):
    try:
        return retrieve(request, get_settings())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

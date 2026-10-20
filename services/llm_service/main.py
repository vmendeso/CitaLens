from functools import lru_cache

from packages.common.config import get_settings
from packages.common.web import create_app
from packages.contracts.assessment import Assessment, AssessmentRequest
from packages.providers.assessment import AssessmentProvider

app = create_app("llm-service")


@lru_cache
def provider():
    return AssessmentProvider(get_settings())


@app.post("/assess", response_model=Assessment)
def assess(request: AssessmentRequest):
    return provider().assess(request)

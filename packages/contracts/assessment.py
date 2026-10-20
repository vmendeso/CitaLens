"""HTTP schemas for provenance-preserving retrieval and semantic assessment."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SemanticStatus = Literal[
    "SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED",
    "NOT_SUPPORTED_BY_REVIEWED_CONTENT", "INCONCLUSIVE", "NOT_EVALUATED",
]


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    quote: str = Field(min_length=1, max_length=12000)


class ModelAssessment(BaseModel):
    """All properties required: compatible with strict structured model outputs."""
    model_config = ConfigDict(extra="forbid")
    semantic_status: SemanticStatus
    rationale: str
    evidence: list[EvidenceQuote]
    limitations: list[str]
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH"]
    suggested_adjustment: str
    reviewed_scope: str


class Assessment(ModelAssessment):
    tokens_used: int = Field(default=0, ge=0)
    prompt_version: str = "assessment-v2"


class AssessmentRequest(BaseModel):
    claim: str | dict[str, Any]
    occurrence: dict[str, Any] = Field(default_factory=dict)
    reference: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    scope: str | dict[str, Any] = ""
    coverage_sufficient: bool = False
    identity_status: Literal["MATCHED", "METADATA_MISMATCH", "UNRESOLVED"]
    access_status: Literal[
        "FULL_TEXT", "PARTIAL_TEXT", "ABSTRACT_ONLY", "METADATA_ONLY",
        "RESTRICTED", "NOT_FOUND", "FETCH_ERROR",
    ]
    mode: Literal["real", "demo"] = "real"
    max_tokens_budget: int | None = Field(default=None, ge=0)


class ResolveRequest(BaseModel):
    reference: dict[str, Any]
    user_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    mode: Literal["real", "demo"] = "real"
    max_external_queries: int | None = Field(default=None, ge=0, le=1000)


class IndexRequest(BaseModel):
    user_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    chunks: list[dict[str, Any]] = Field(max_length=1000)


class SearchRequest(BaseModel):
    user_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    version: str | int
    query: str = Field(min_length=1, max_length=10000)
    limit: int = Field(default=10, ge=1, le=50)


class RetrieveRequest(BaseModel):
    user_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    version: str | int
    claim: str | dict[str, Any]
    chunks: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)


def claim_text(value: str | dict[str, Any]) -> str:
    return value if isinstance(value, str) else str(value.get("text", value.get("claim", "")))

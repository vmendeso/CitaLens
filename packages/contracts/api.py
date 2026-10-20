from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    INGESTING = "INGESTING"
    EXTRACTING_REFERENCES = "EXTRACTING_REFERENCES"
    MAPPING_CITATIONS = "MAPPING_CITATIONS"
    RESOLVING_SOURCES = "RESOLVING_SOURCES"
    EVALUATING = "EVALUATING"
    GENERATING_REPORT = "GENERATING_REPORT"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


STAGES = ["INGESTING", "EXTRACTING_REFERENCES", "MAPPING_CITATIONS",
          "RESOLVING_SOURCES", "EVALUATING", "GENERATING_REPORT"]
TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254, examples=["pesquisador@example.org"])
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)


class AnalysisOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["real", "demo"] = "real"
    citation_style: Literal["unspecified", "ABNT", "APA", "Vancouver"] = "unspecified"
    language: Literal["pt", "en"] = "pt"
    bibliography_page_start: int | None = Field(None, ge=1)
    bibliography_page_end: int | None = Field(None, ge=1)
    copy_sources_from: UUID | None = None

    @model_validator(mode="after")
    def page_interval(self):
        start, end = self.bibliography_page_start, self.bibliography_page_end
        if (start is None) != (end is None) or (start and end < start):
            raise ValueError("Informe um intervalo completo de páginas em ordem crescente.")
        return self


class PageEnvelope(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int = 50
    offset: int = 0


class CitationReview(BaseModel):
    reference_id: UUID
    comment: str = Field(min_length=3, max_length=4000)


class FindingReview(BaseModel):
    decision: Literal["AGREE", "DISAGREE", "NEEDS_REVIEW"]
    comment: str = Field(min_length=3, max_length=4000)


class StageCommand(BaseModel):
    run_id: UUID
    event_version: Literal[1] = 1


class Accepted(BaseModel):
    id: UUID
    status: str
    progress_url: str

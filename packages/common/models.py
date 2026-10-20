"""Relational source of truth. JSONB stores extraction/provider details, never core links."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, Enum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from packages.contracts.api import RunStatus


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Identified:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class User(Identified, Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    documents: Mapped[list["Document"]] = relationship(back_populates="owner", passive_deletes=True)


class Document(Identified, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_document_owner"),
        {"schema": "orchestration"},
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleting: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deletion_error: Mapped[str | None] = mapped_column(Text)
    owner: Mapped["User"] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", passive_deletes=True, foreign_keys="DocumentVersion.document_id")


class DocumentVersion(Identified, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
        UniqueConstraint("id", "document_id", name="uq_version_document"),
        CheckConstraint("version > 0 AND page_count > 0", name="ck_version_counts"),
        {"schema": "orchestration"},
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orchestration.documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped["Document"] = relationship(back_populates="versions", foreign_keys=[document_id])
    pages: Mapped[list["Page"]] = relationship(back_populates="document_version", passive_deletes=True)


class AnalysisRun(Identified, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "user_id"], ["orchestration.documents.id",
                            "orchestration.documents.user_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_version_id", "document_id"],
                            ["orchestration.document_versions.id", "orchestration.document_versions.document_id"],
                            ondelete="CASCADE"),
        UniqueConstraint("id", "document_id", "user_id", name="uq_run_scope"),
        CheckConstraint("tokens_used >= 0 AND external_queries >= 0", name="ck_run_usage"),
        {"schema": "orchestration"},
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", schema="orchestration"), default=RunStatus.QUEUED, index=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    external_queries: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps: Mapped[list["AnalysisStep"]] = relationship(back_populates="run", passive_deletes=True)


class AnalysisStep(Identified, Base):
    __tablename__ = "analysis_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_run_step"),
        CheckConstraint("attempts >= 0", name="ck_step_attempts"),
        {"schema": "orchestration"},
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orchestration.analysis_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run: Mapped["AnalysisRun"] = relationship(back_populates="steps")


class OutboxEvent(Identified, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_outbox_key"),
        CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        {"schema": "orchestration"},
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orchestration.analysis_runs.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(200))
    task_name: Mapped[str] = mapped_column(String(100))
    queue: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivery_state: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    __table_args__ = {"schema": "orchestration"}
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orchestration.outbox_events.id", ondelete="CASCADE"), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StoredFile(Identified, Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("user_id", "object_key", name="uq_file_object"),
        CheckConstraint("size_bytes > 0 AND page_count > 0", name="ck_file_size"),
        {"schema": "storage"},
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    object_key: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))


class Page(Identified, Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "physical_page_number", name="uq_page_number"),
        CheckConstraint("physical_index >= 0 AND physical_page_number = physical_index + 1", name="ck_page_number"),
        {"schema": "knowledge"},
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orchestration.document_versions.id", ondelete="CASCADE"), index=True)
    physical_index: Mapped[int] = mapped_column(Integer)
    physical_page_number: Mapped[int] = mapped_column(Integer)
    printed_label: Mapped[str | None] = mapped_column(String(40))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    extraction_method: Mapped[str] = mapped_column(String(30))
    blocks: Mapped[list] = mapped_column(JSONB, default=list)
    quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    document_version: Mapped["DocumentVersion"] = relationship(back_populates="pages")


class RunOwned:
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orchestration.analysis_runs.id", ondelete="CASCADE"), index=True)


class PageCoverage(Identified, RunOwned, Base):
    __tablename__ = "page_coverage"
    __table_args__ = (
        UniqueConstraint("run_id", "physical_page_number", name="uq_coverage_page"),
        CheckConstraint("physical_page_number > 0", name="ck_coverage_page"),
        {"schema": "knowledge"},
    )
    physical_page_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class KnowledgeCheckpoint(Identified, RunOwned, Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "stage", name="uq_knowledge_stage"), {"schema": "knowledge"})
    stage: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)


class BibliographicWork(Identified, Base):
    __tablename__ = "bibliographic_works"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "user_id"], ["orchestration.documents.id",
                            "orchestration.documents.user_id"], ondelete="CASCADE"),
        UniqueConstraint("id", "document_id", name="uq_work_document"),
        Index("uq_work_doi", "document_id", "normalized_doi", unique=True,
              postgresql_where=__import__("sqlalchemy").text("normalized_doi IS NOT NULL")),
        {"schema": "knowledge"},
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    normalized_doi: Mapped[str | None] = mapped_column(String(500))
    title: Mapped[str | None] = mapped_column(Text)
    metadata_record: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReferenceEntry(Identified, RunOwned, Base):
    __tablename__ = "reference_entries"
    __table_args__ = (
        UniqueConstraint("id", "run_id", name="uq_reference_scope"),
        CheckConstraint("page_start > 0 AND page_end >= page_start", name="ck_reference_pages"),
        {"schema": "knowledge"},
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.bibliographic_works.id", ondelete="SET NULL"), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSONB, default=list)
    title: Mapped[str | None] = mapped_column(Text)
    year: Mapped[str | None] = mapped_column(String(10))
    year_suffix: Mapped[str | None] = mapped_column(String(4))
    doi: Mapped[str | None] = mapped_column(String(500))
    isbn: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text)
    entry_number: Mapped[int | None] = mapped_column(Integer)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    issues: Mapped[list] = mapped_column(JSONB, default=list)
    identity_status: Mapped[str] = mapped_column(String(30), default="UNRESOLVED")
    access_status: Mapped[str] = mapped_column(String(30), default="NOT_FOUND")
    source_resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class CitationOccurrence(Identified, RunOwned, Base):
    __tablename__ = "citation_occurrences"
    __table_args__ = (
        UniqueConstraint("id", "run_id", name="uq_occurrence_scope"),
        CheckConstraint("page_start > 0 AND page_end >= page_start", name="ck_citation_pages"),
        CheckConstraint("offset_start >= 0 AND offset_end >= offset_start", name="ck_citation_offsets"),
        {"schema": "knowledge"},
    )
    marker: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer, index=True)
    page_end: Mapped[int] = mapped_column(Integer)
    offset_start: Mapped[int] = mapped_column(Integer)
    offset_end: Mapped[int] = mapped_column(Integer)
    sentence: Mapped[str] = mapped_column(Text)
    paragraph: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text)
    binding_status: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Claim(Identified, RunOwned, Base):
    __tablename__ = "claims"
    __table_args__ = (UniqueConstraint("id", "run_id", name="uq_claim_scope"), {"schema": "knowledge"})
    text: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class OccurrenceReference(Base):
    __tablename__ = "occurrence_references"
    __table_args__ = (
        ForeignKeyConstraint(["occurrence_id", "run_id"],
                            ["knowledge.citation_occurrences.id", "knowledge.citation_occurrences.run_id"],
                            ondelete="CASCADE"),
        ForeignKeyConstraint(["reference_id", "run_id"],
                            ["knowledge.reference_entries.id", "knowledge.reference_entries.run_id"],
                            ondelete="CASCADE"),
        {"schema": "knowledge"},
    )
    occurrence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation: Mapped[str] = mapped_column(String(20), default="RESOLVED")


class ClaimOccurrence(Base):
    __tablename__ = "claim_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(["claim_id", "run_id"], ["knowledge.claims.id", "knowledge.claims.run_id"],
                            ondelete="CASCADE"),
        ForeignKeyConstraint(["occurrence_id", "run_id"],
                            ["knowledge.citation_occurrences.id", "knowledge.citation_occurrences.run_id"],
                            ondelete="CASCADE"),
        {"schema": "knowledge"},
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class ClaimReference(Base):
    __tablename__ = "claim_references"
    __table_args__ = (
        ForeignKeyConstraint(["claim_id", "run_id"], ["knowledge.claims.id", "knowledge.claims.run_id"],
                            ondelete="CASCADE"),
        ForeignKeyConstraint(["reference_id", "run_id"],
                            ["knowledge.reference_entries.id", "knowledge.reference_entries.run_id"],
                            ondelete="CASCADE"),
        {"schema": "knowledge"},
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class SourceDocument(Identified, RunOwned, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("id", "work_id", "version", name="uq_source_work_version"),
        {"schema": "knowledge"},
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.bibliographic_works.id", ondelete="CASCADE"), index=True)
    reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.reference_entries.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(100), default="1")
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    origin_url: Mapped[str | None] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    access_status: Mapped[str] = mapped_column(String(30))
    identity_status: Mapped[str] = mapped_column(String(30))
    user_attached: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class SearchAttempt(Identified, RunOwned, Base):
    __tablename__ = "search_attempts"
    __table_args__ = {"schema": "knowledge"}
    reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.reference_entries.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    query: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Chunk(Identified, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "user_id"], ["orchestration.documents.id",
                            "orchestration.documents.user_id"], ondelete="CASCADE"),
        CheckConstraint("page_start > 0 AND page_end >= page_start", name="ck_chunk_pages"),
        CheckConstraint("offset_start >= 0 AND offset_end >= offset_start AND ordinal >= 0",
                        name="ck_chunk_offsets"),
        CheckConstraint("(origin = 'SOURCE' AND source_id IS NOT NULL AND work_id IS NOT NULL) "
                        "OR (origin = 'MANUSCRIPT' AND source_id IS NULL AND work_id IS NULL)",
                        name="ck_chunk_origin"),
        {"schema": "knowledge"},
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orchestration.document_versions.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.source_documents.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.bibliographic_works.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(20))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    offset_start: Mapped[int] = mapped_column(Integer)
    offset_end: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    index_state: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    embedding: Mapped[dict] = mapped_column(JSONB, default=dict)


class EvidencePassage(Identified, RunOwned, Base):
    __tablename__ = "evidence_passages"
    __table_args__ = (
        ForeignKeyConstraint(["source_id", "work_id", "version"],
                            ["knowledge.source_documents.id", "knowledge.source_documents.work_id",
                             "knowledge.source_documents.version"], ondelete="CASCADE"),
        UniqueConstraint("id", "run_id", name="uq_evidence_scope"),
        CheckConstraint("page_start > 0 AND page_end >= page_start", name="ck_evidence_pages"),
        {"schema": "knowledge"},
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    work_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class CitationAssessment(Identified, RunOwned, Base):
    __tablename__ = "citation_assessments"
    __table_args__ = (
        ForeignKeyConstraint(["claim_id", "run_id"], ["knowledge.claims.id", "knowledge.claims.run_id"],
                            ondelete="CASCADE"),
        ForeignKeyConstraint(["occurrence_id", "run_id"],
                            ["knowledge.citation_occurrences.id", "knowledge.citation_occurrences.run_id"],
                            ondelete="CASCADE"),
        ForeignKeyConstraint(["reference_id", "run_id"],
                            ["knowledge.reference_entries.id", "knowledge.reference_entries.run_id"],
                            ondelete="CASCADE"),
        UniqueConstraint("pair_key", "version", name="uq_assessment_version"),
        UniqueConstraint("id", "run_id", name="uq_assessment_scope"),
        CheckConstraint("version > 0 AND tokens_used >= 0", name="ck_assessment_counts"),
        {"schema": "knowledge"},
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    pair_key: Mapped[str] = mapped_column(String(150))
    version: Mapped[int] = mapped_column(Integer, default=1)
    binding_status: Mapped[str] = mapped_column(String(30))
    identity_status: Mapped[str] = mapped_column(String(30))
    access_status: Mapped[str] = mapped_column(String(30))
    semantic_status: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    rationale: Mapped[str] = mapped_column(Text)
    suggested_adjustment: Mapped[str | None] = mapped_column(Text)
    reviewed_scope: Mapped[str] = mapped_column(Text)
    limitations: Mapped[list] = mapped_column(JSONB, default=list)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class AssessmentEvidence(Base):
    __tablename__ = "assessment_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["assessment_id", "run_id"],
                            ["knowledge.citation_assessments.id", "knowledge.citation_assessments.run_id"],
                            ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "run_id"],
                            ["knowledge.evidence_passages.id", "knowledge.evidence_passages.run_id"],
                            ondelete="CASCADE"),
        {"schema": "knowledge"},
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quote: Mapped[str] = mapped_column(Text)


class ReviewAction(Identified, RunOwned, Base):
    __tablename__ = "review_actions"
    __table_args__ = {"schema": "knowledge"}
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.users.id", ondelete="CASCADE"))
    occurrence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.citation_occurrences.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.citation_assessments.id", ondelete="CASCADE"), index=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.reference_entries.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text)
    previous: Mapped[dict] = mapped_column(JSONB)


class Report(Identified, RunOwned, Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_report_version"),
        CheckConstraint("version > 0", name="ck_report_version"),
        {"schema": "orchestration"},
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64))

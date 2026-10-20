CREATE SCHEMA identity;

CREATE SCHEMA orchestration;

CREATE SCHEMA storage;

CREATE SCHEMA knowledge;

CREATE TYPE orchestration.run_status AS ENUM ('QUEUED', 'INGESTING', 'EXTRACTING_REFERENCES', 'MAPPING_CITATIONS', 'RESOLVING_SOURCES', 'EVALUATING', 'GENERATING_REPORT', 'COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED');

CREATE TABLE identity.users (
	email VARCHAR(254) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	password_hash TEXT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE storage.files (
	user_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	object_key VARCHAR(200) NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	page_count INTEGER NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_file_object UNIQUE (user_id, object_key), 
	CONSTRAINT ck_file_size CHECK (size_bytes > 0 AND page_count > 0)
);

CREATE INDEX ix_storage_files_document_id ON storage.files (document_id);

CREATE INDEX ix_storage_files_user_id ON storage.files (user_id);

CREATE TABLE orchestration.documents (
	user_id UUID NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	current_version_id UUID, 
	deleting BOOLEAN NOT NULL, 
	deletion_error TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_document_owner UNIQUE (id, user_id), 
	FOREIGN KEY(user_id) REFERENCES identity.users (id) ON DELETE CASCADE
);

CREATE INDEX ix_orchestration_documents_user_id ON orchestration.documents (user_id);

CREATE TABLE knowledge.bibliographic_works (
	user_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	normalized_doi VARCHAR(500), 
	title TEXT, 
	metadata_record JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id, user_id) REFERENCES orchestration.documents (id, user_id) ON DELETE CASCADE, 
	CONSTRAINT uq_work_document UNIQUE (id, document_id)
);

CREATE INDEX ix_knowledge_bibliographic_works_document_id ON knowledge.bibliographic_works (document_id);

CREATE INDEX ix_knowledge_bibliographic_works_user_id ON knowledge.bibliographic_works (user_id);

CREATE UNIQUE INDEX uq_work_doi ON knowledge.bibliographic_works (document_id, normalized_doi) WHERE normalized_doi IS NOT NULL;

CREATE TABLE orchestration.document_versions (
	document_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	file_id UUID NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	page_count INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_document_version UNIQUE (document_id, version), 
	CONSTRAINT uq_version_document UNIQUE (id, document_id), 
	CONSTRAINT ck_version_counts CHECK (version > 0 AND page_count > 0), 
	FOREIGN KEY(document_id) REFERENCES orchestration.documents (id) ON DELETE CASCADE
);

CREATE INDEX ix_orchestration_document_versions_document_id ON orchestration.document_versions (document_id);

CREATE TABLE knowledge.pages (
	document_version_id UUID NOT NULL, 
	physical_index INTEGER NOT NULL, 
	physical_page_number INTEGER NOT NULL, 
	printed_label VARCHAR(40), 
	raw_text TEXT NOT NULL, 
	normalized_text TEXT NOT NULL, 
	extraction_method VARCHAR(30) NOT NULL, 
	blocks JSONB NOT NULL, 
	quality JSONB NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	error TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_page_number UNIQUE (document_version_id, physical_page_number), 
	CONSTRAINT ck_page_number CHECK (physical_index >= 0 AND physical_page_number = physical_index + 1), 
	FOREIGN KEY(document_version_id) REFERENCES orchestration.document_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_pages_document_version_id ON knowledge.pages (document_version_id);

CREATE TABLE orchestration.analysis_runs (
	document_id UUID NOT NULL, 
	document_version_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	status orchestration.run_status NOT NULL, 
	config JSONB NOT NULL, 
	summary JSONB NOT NULL, 
	warnings JSONB NOT NULL, 
	cancel_requested BOOLEAN NOT NULL, 
	error TEXT, 
	tokens_used INTEGER NOT NULL, 
	external_queries INTEGER NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id, user_id) REFERENCES orchestration.documents (id, user_id) ON DELETE CASCADE, 
	FOREIGN KEY(document_version_id, document_id) REFERENCES orchestration.document_versions (id, document_id) ON DELETE CASCADE, 
	CONSTRAINT uq_run_scope UNIQUE (id, document_id, user_id), 
	CONSTRAINT ck_run_usage CHECK (tokens_used >= 0 AND external_queries >= 0)
);

CREATE INDEX ix_orchestration_analysis_runs_document_id ON orchestration.analysis_runs (document_id);

CREATE INDEX ix_orchestration_analysis_runs_document_version_id ON orchestration.analysis_runs (document_version_id);

CREATE INDEX ix_orchestration_analysis_runs_status ON orchestration.analysis_runs (status);

CREATE INDEX ix_orchestration_analysis_runs_user_id ON orchestration.analysis_runs (user_id);

CREATE TABLE knowledge.checkpoints (
	stage VARCHAR(40) NOT NULL, 
	result JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_knowledge_stage UNIQUE (run_id, stage), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_checkpoints_run_id ON knowledge.checkpoints (run_id);

CREATE TABLE knowledge.citation_occurrences (
	marker TEXT NOT NULL, 
	page_start INTEGER NOT NULL, 
	page_end INTEGER NOT NULL, 
	offset_start INTEGER NOT NULL, 
	offset_end INTEGER NOT NULL, 
	sentence TEXT NOT NULL, 
	paragraph TEXT NOT NULL, 
	context TEXT NOT NULL, 
	binding_status VARCHAR(30) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_occurrence_scope UNIQUE (id, run_id), 
	CONSTRAINT ck_citation_pages CHECK (page_start > 0 AND page_end >= page_start), 
	CONSTRAINT ck_citation_offsets CHECK (offset_start >= 0 AND offset_end >= offset_start), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_citation_occurrences_binding_status ON knowledge.citation_occurrences (binding_status);

CREATE INDEX ix_knowledge_citation_occurrences_page_start ON knowledge.citation_occurrences (page_start);

CREATE INDEX ix_knowledge_citation_occurrences_run_id ON knowledge.citation_occurrences (run_id);

CREATE TABLE knowledge.claims (
	text TEXT NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_claim_scope UNIQUE (id, run_id), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_claims_run_id ON knowledge.claims (run_id);

CREATE TABLE knowledge.page_coverage (
	physical_page_number INTEGER NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	error TEXT, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_coverage_page UNIQUE (run_id, physical_page_number), 
	CONSTRAINT ck_coverage_page CHECK (physical_page_number > 0), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_page_coverage_run_id ON knowledge.page_coverage (run_id);

CREATE INDEX ix_knowledge_page_coverage_status ON knowledge.page_coverage (status);

CREATE TABLE knowledge.reference_entries (
	work_id UUID, 
	raw_text TEXT NOT NULL, 
	authors JSONB NOT NULL, 
	title TEXT, 
	year VARCHAR(10), 
	year_suffix VARCHAR(4), 
	doi VARCHAR(500), 
	isbn VARCHAR(100), 
	url TEXT, 
	entry_number INTEGER, 
	page_start INTEGER NOT NULL, 
	page_end INTEGER NOT NULL, 
	details JSONB NOT NULL, 
	issues JSONB NOT NULL, 
	identity_status VARCHAR(30) NOT NULL, 
	access_status VARCHAR(30) NOT NULL, 
	source_resolved BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_reference_scope UNIQUE (id, run_id), 
	CONSTRAINT ck_reference_pages CHECK (page_start > 0 AND page_end >= page_start), 
	FOREIGN KEY(work_id) REFERENCES knowledge.bibliographic_works (id) ON DELETE SET NULL, 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_reference_entries_run_id ON knowledge.reference_entries (run_id);

CREATE INDEX ix_knowledge_reference_entries_work_id ON knowledge.reference_entries (work_id);

CREATE TABLE orchestration.analysis_steps (
	run_id UUID NOT NULL, 
	name VARCHAR(40) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	attempts INTEGER NOT NULL, 
	result JSONB NOT NULL, 
	error TEXT, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_run_step UNIQUE (run_id, name), 
	CONSTRAINT ck_step_attempts CHECK (attempts >= 0), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_orchestration_analysis_steps_run_id ON orchestration.analysis_steps (run_id);

CREATE TABLE orchestration.outbox_events (
	run_id UUID, 
	event_key VARCHAR(200) NOT NULL, 
	task_name VARCHAR(100) NOT NULL, 
	queue VARCHAR(30) NOT NULL, 
	payload JSONB NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	delivery_state VARCHAR(20) NOT NULL, 
	last_error TEXT, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_outbox_key UNIQUE (event_key), 
	CONSTRAINT ck_outbox_attempts CHECK (attempts >= 0), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_orchestration_outbox_events_delivery_state ON orchestration.outbox_events (delivery_state);

CREATE INDEX ix_orchestration_outbox_events_published_at ON orchestration.outbox_events (published_at);

CREATE INDEX ix_orchestration_outbox_events_run_id ON orchestration.outbox_events (run_id);

CREATE TABLE orchestration.reports (
	version INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	file_id UUID, 
	snapshot JSONB NOT NULL, 
	content_hash VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_report_version UNIQUE (run_id, version), 
	CONSTRAINT ck_report_version CHECK (version > 0), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_orchestration_reports_run_id ON orchestration.reports (run_id);

CREATE TABLE knowledge.citation_assessments (
	claim_id UUID NOT NULL, 
	occurrence_id UUID NOT NULL, 
	reference_id UUID, 
	pair_key VARCHAR(150) NOT NULL, 
	version INTEGER NOT NULL, 
	binding_status VARCHAR(30) NOT NULL, 
	identity_status VARCHAR(30) NOT NULL, 
	access_status VARCHAR(30) NOT NULL, 
	semantic_status VARCHAR(50) NOT NULL, 
	severity VARCHAR(20) NOT NULL, 
	rationale TEXT NOT NULL, 
	suggested_adjustment TEXT, 
	reviewed_scope TEXT NOT NULL, 
	limitations JSONB NOT NULL, 
	tokens_used INTEGER NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(claim_id, run_id) REFERENCES knowledge.claims (id, run_id) ON DELETE CASCADE, 
	FOREIGN KEY(occurrence_id, run_id) REFERENCES knowledge.citation_occurrences (id, run_id) ON DELETE CASCADE, 
	FOREIGN KEY(reference_id, run_id) REFERENCES knowledge.reference_entries (id, run_id) ON DELETE CASCADE, 
	CONSTRAINT uq_assessment_version UNIQUE (pair_key, version), 
	CONSTRAINT uq_assessment_scope UNIQUE (id, run_id), 
	CONSTRAINT ck_assessment_counts CHECK (version > 0 AND tokens_used >= 0), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_citation_assessments_claim_id ON knowledge.citation_assessments (claim_id);

CREATE INDEX ix_knowledge_citation_assessments_occurrence_id ON knowledge.citation_assessments (occurrence_id);

CREATE INDEX ix_knowledge_citation_assessments_reference_id ON knowledge.citation_assessments (reference_id);

CREATE INDEX ix_knowledge_citation_assessments_run_id ON knowledge.citation_assessments (run_id);

CREATE INDEX ix_knowledge_citation_assessments_semantic_status ON knowledge.citation_assessments (semantic_status);

CREATE INDEX ix_knowledge_citation_assessments_severity ON knowledge.citation_assessments (severity);

CREATE TABLE knowledge.claim_occurrences (
	claim_id UUID NOT NULL, 
	occurrence_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (claim_id, occurrence_id), 
	FOREIGN KEY(claim_id, run_id) REFERENCES knowledge.claims (id, run_id) ON DELETE CASCADE, 
	FOREIGN KEY(occurrence_id, run_id) REFERENCES knowledge.citation_occurrences (id, run_id) ON DELETE CASCADE
);

CREATE TABLE knowledge.claim_references (
	claim_id UUID NOT NULL, 
	reference_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (claim_id, reference_id), 
	FOREIGN KEY(claim_id, run_id) REFERENCES knowledge.claims (id, run_id) ON DELETE CASCADE, 
	FOREIGN KEY(reference_id, run_id) REFERENCES knowledge.reference_entries (id, run_id) ON DELETE CASCADE
);

CREATE TABLE knowledge.occurrence_references (
	occurrence_id UUID NOT NULL, 
	reference_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	relation VARCHAR(20) NOT NULL, 
	PRIMARY KEY (occurrence_id, reference_id), 
	FOREIGN KEY(occurrence_id, run_id) REFERENCES knowledge.citation_occurrences (id, run_id) ON DELETE CASCADE, 
	FOREIGN KEY(reference_id, run_id) REFERENCES knowledge.reference_entries (id, run_id) ON DELETE CASCADE
);

CREATE TABLE knowledge.search_attempts (
	reference_id UUID NOT NULL, 
	provider VARCHAR(80) NOT NULL, 
	query TEXT NOT NULL, 
	outcome VARCHAR(80) NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(reference_id) REFERENCES knowledge.reference_entries (id) ON DELETE CASCADE, 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_search_attempts_reference_id ON knowledge.search_attempts (reference_id);

CREATE INDEX ix_knowledge_search_attempts_run_id ON knowledge.search_attempts (run_id);

CREATE TABLE knowledge.source_documents (
	work_id UUID NOT NULL, 
	reference_id UUID NOT NULL, 
	version VARCHAR(100) NOT NULL, 
	file_id UUID, 
	content_hash VARCHAR(64) NOT NULL, 
	origin_url TEXT, 
	final_url TEXT, 
	accessed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	access_status VARCHAR(30) NOT NULL, 
	identity_status VARCHAR(30) NOT NULL, 
	user_attached BOOLEAN NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_source_work_version UNIQUE (id, work_id, version), 
	FOREIGN KEY(work_id) REFERENCES knowledge.bibliographic_works (id) ON DELETE CASCADE, 
	FOREIGN KEY(reference_id) REFERENCES knowledge.reference_entries (id) ON DELETE CASCADE, 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_source_documents_reference_id ON knowledge.source_documents (reference_id);

CREATE INDEX ix_knowledge_source_documents_run_id ON knowledge.source_documents (run_id);

CREATE INDEX ix_knowledge_source_documents_work_id ON knowledge.source_documents (work_id);

CREATE TABLE orchestration.processed_events (
	event_id UUID NOT NULL, 
	processed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(event_id) REFERENCES orchestration.outbox_events (id) ON DELETE CASCADE
);

CREATE TABLE knowledge.chunks (
	user_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	document_version_id UUID NOT NULL, 
	source_id UUID, 
	work_id UUID, 
	version VARCHAR(100) NOT NULL, 
	origin VARCHAR(20) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	text TEXT NOT NULL, 
	page_start INTEGER NOT NULL, 
	page_end INTEGER NOT NULL, 
	offset_start INTEGER NOT NULL, 
	offset_end INTEGER NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	index_state VARCHAR(20) NOT NULL, 
	embedding JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id, user_id) REFERENCES orchestration.documents (id, user_id) ON DELETE CASCADE, 
	CONSTRAINT ck_chunk_pages CHECK (page_start > 0 AND page_end >= page_start), 
	CONSTRAINT ck_chunk_offsets CHECK (offset_start >= 0 AND offset_end >= offset_start AND ordinal >= 0), 
	CONSTRAINT ck_chunk_origin CHECK ((origin = 'SOURCE' AND source_id IS NOT NULL AND work_id IS NOT NULL) OR (origin = 'MANUSCRIPT' AND source_id IS NULL AND work_id IS NULL)), 
	FOREIGN KEY(document_version_id) REFERENCES orchestration.document_versions (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_id) REFERENCES knowledge.source_documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(work_id) REFERENCES knowledge.bibliographic_works (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_chunks_document_id ON knowledge.chunks (document_id);

CREATE INDEX ix_knowledge_chunks_index_state ON knowledge.chunks (index_state);

CREATE INDEX ix_knowledge_chunks_source_id ON knowledge.chunks (source_id);

CREATE INDEX ix_knowledge_chunks_user_id ON knowledge.chunks (user_id);

CREATE INDEX ix_knowledge_chunks_work_id ON knowledge.chunks (work_id);

CREATE TABLE knowledge.evidence_passages (
	source_id UUID NOT NULL, 
	work_id UUID NOT NULL, 
	version VARCHAR(100) NOT NULL, 
	text TEXT NOT NULL, 
	page_start INTEGER NOT NULL, 
	page_end INTEGER NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(source_id, work_id, version) REFERENCES knowledge.source_documents (id, work_id, version) ON DELETE CASCADE, 
	CONSTRAINT uq_evidence_scope UNIQUE (id, run_id), 
	CONSTRAINT ck_evidence_pages CHECK (page_start > 0 AND page_end >= page_start), 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_evidence_passages_run_id ON knowledge.evidence_passages (run_id);

CREATE INDEX ix_knowledge_evidence_passages_source_id ON knowledge.evidence_passages (source_id);

CREATE TABLE knowledge.review_actions (
	user_id UUID NOT NULL, 
	occurrence_id UUID, 
	assessment_id UUID, 
	reference_id UUID, 
	decision VARCHAR(30) NOT NULL, 
	comment TEXT NOT NULL, 
	previous JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	run_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES identity.users (id) ON DELETE CASCADE, 
	FOREIGN KEY(occurrence_id) REFERENCES knowledge.citation_occurrences (id) ON DELETE CASCADE, 
	FOREIGN KEY(assessment_id) REFERENCES knowledge.citation_assessments (id) ON DELETE CASCADE, 
	FOREIGN KEY(reference_id) REFERENCES knowledge.reference_entries (id) ON DELETE CASCADE, 
	FOREIGN KEY(run_id) REFERENCES orchestration.analysis_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_knowledge_review_actions_assessment_id ON knowledge.review_actions (assessment_id);

CREATE INDEX ix_knowledge_review_actions_occurrence_id ON knowledge.review_actions (occurrence_id);

CREATE INDEX ix_knowledge_review_actions_run_id ON knowledge.review_actions (run_id);

CREATE TABLE knowledge.assessment_evidence (
	assessment_id UUID NOT NULL, 
	evidence_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	quote TEXT NOT NULL, 
	PRIMARY KEY (assessment_id, evidence_id), 
	FOREIGN KEY(assessment_id, run_id) REFERENCES knowledge.citation_assessments (id, run_id) ON DELETE CASCADE, 
	FOREIGN KEY(evidence_id, run_id) REFERENCES knowledge.evidence_passages (id, run_id) ON DELETE CASCADE
);

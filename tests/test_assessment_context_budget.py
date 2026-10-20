"""Whole-paragraph retrieval must fit the paid call and report its actual scope."""
from copy import deepcopy
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest

from packages.common.config import Settings
from packages.contracts.assessment import AssessmentRequest, RetrieveRequest
from packages.providers.assessment import (
    AssessmentProvider, context_token_bound, fit_assessment_context,
)
from packages.providers.retrieval import retrieve
from packages.providers.http import ProviderError


SOURCE = str(uuid.uuid5(uuid.NAMESPACE_URL, "test/source"))
WORK = str(uuid.uuid5(uuid.NAMESPACE_URL, "test/work"))


def paragraph(index, text=None):
    text = text or ("Observações clínicas descrevem a recuperação e as condições da população avaliada. " * 10)[:590]
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"test/paragraph/{index}")),
        "user_id": "user", "document_id": "document", "document_version_id": "version",
        "source_id": SOURCE, "work_id": WORK, "version": "1", "origin": "SOURCE",
        "text": text, "ordinal": index, "page_start": index // 5 + 1, "page_end": index // 5 + 1,
        "offset_start": 0, "offset_end": len(text),
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
    }


def assessment_request(evidence, **changes):
    return AssessmentRequest(**({
        "claim": "O tratamento foi associado à recuperação observada.",
        "reference": {"title": "Estudo clínico", "authors": ["Silva"], "work_id": WORK},
        "source": {"id": SOURCE, "work_id": WORK, "version": "1", "user_id": "user", "document_id": "document"},
        "evidence": evidence, "identity_status": "MATCHED", "access_status": "FULL_TEXT",
        "scope": {"all_available_content": True, "available_chunks": len(evidence),
                  "reviewed_chunks": len(evidence), "reviewed_pages": [1, 2, 3, 4, 5, 6, 7]},
        "coverage_sufficient": True, "max_tokens_budget": 32000,
    } | changes))


def large_retrieval():
    chunks = [paragraph(index) for index in range(35)]
    request = RetrieveRequest(user_id="user", document_id="document", source_id=SOURCE,
                              work_id=WORK, version="1", claim="Recovery after treatment", chunks=chunks)
    # A multilingual semantic hit is on the final page, not the first paragraphs.
    result = retrieve(request, Settings(_env_file=None), vector_search=lambda _: {"chunks": [chunks[-1]]})
    return chunks, result


def test_complete_source_that_exceeds_token_budget_keeps_relevant_whole_paragraphs():
    chunks, rag = large_retrieval()
    assert rag["coverage_sufficient"]
    assert len(rag["evidence"]) == 35
    request = assessment_request(rag["evidence"], scope=rag["scope"])
    original = deepcopy(request.model_dump())
    assert context_token_bound(request) > 32000

    fitted, limitations = fit_assessment_context(request)
    assert 0 < len(fitted.evidence) < 35
    assert chunks[-1]["id"] in {item["id"] for item in fitted.evidence}
    assert context_token_bound(fitted) <= 32000
    assert fitted.max_tokens_budget == request.max_tokens_budget
    assert not fitted.coverage_sufficient
    assert fitted.scope["all_available_content"] is False
    assert fitted.scope["available_chunks"] == 35
    assert fitted.scope["reviewed_chunks"] == len(fitted.evidence)
    assert fitted.scope["reviewed_pages"] == sorted({item["page_start"] for item in fitted.evidence})
    assert limitations
    originals = {item["id"]: item for item in chunks}
    assert all(item["text"] == originals[item["id"]]["text"] for item in fitted.evidence)
    assert request.model_dump() == original
    repeated, _ = fit_assessment_context(fitted)
    assert repeated.model_dump() == fitted.model_dump()


@pytest.mark.parametrize("status, expected", [
    ("SUPPORTED", "SUPPORTED"),
    ("NOT_SUPPORTED_BY_REVIEWED_CONTENT", "INCONCLUSIVE"),
])
def test_provider_calls_model_within_budget_and_rejects_negative_conclusion_after_trimming(monkeypatch, status, expected):
    chunks, rag = large_retrieval()
    provider = AssessmentProvider(Settings(_env_file=None, llm_provider="groq", groq_api_key="test-secret"))
    calls = []

    def model(*args, **kwargs):
        outgoing = kwargs["payload"]
        calls.append(outgoing)
        context = json.loads(outgoing["messages"][1]["content"])
        assert context["scope"]["all_available_content"] is False
        assert not context["coverage_sufficient"]
        assert chunks[-1]["id"] in {item["id"] for item in context["evidence"]}
        serialized = json.dumps({"messages": outgoing["messages"],
                                 "schema": outgoing["response_format"]["json_schema"]["schema"]}, ensure_ascii=False)
        assert len(serialized.encode("utf-8")) + outgoing["max_completion_tokens"] + 512 <= 32000
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "semantic_status": status, "rationale": "A passagem descreve as observações.",
            "evidence": [{"evidence_id": chunks[-1]["id"], "quote": "Observações clínicas"}],
            "limitations": [], "severity": "INFO", "suggested_adjustment": "Manter a delimitação.",
            "reviewed_scope": "Texto disponibilizado",
        })}}], "usage": {"total_tokens": 9000}}

    monkeypatch.setattr(provider.http, "request", model)
    result = provider.assess(assessment_request(rag["evidence"], scope=rag["scope"]))
    assert len(calls) == 1
    assert result.semantic_status == expected
    assert result.tokens_used == 9000
    assert json.loads(result.reviewed_scope)["all_available_content"] is False
    assert any("cobertura parcial" in limitation for limitation in result.limitations)


def test_small_complete_source_keeps_full_coverage_without_selection():
    request = assessment_request([paragraph(0, "A intervenção foi observada em adultos.")])
    fitted, limitations = fit_assessment_context(request)
    assert fitted is request
    assert fitted.coverage_sufficient
    assert fitted.scope["all_available_content"]
    assert limitations == []


def test_no_paragraph_can_fit_does_not_call_model_or_claim_review(monkeypatch):
    provider = AssessmentProvider(Settings(_env_file=None, llm_provider="groq", groq_api_key="test-secret"))
    monkeypatch.setattr(provider.http, "request", lambda *args, **kwargs: pytest.fail("No room for a complete call."))
    result = provider.assess(assessment_request([paragraph(0)], max_tokens_budget=100))
    assert result.semantic_status == "INCONCLUSIVE"
    assert result.tokens_used == 0
    scope = json.loads(result.reviewed_scope)
    assert scope["reviewed_chunks"] == 0
    assert scope["reviewed_pages"] == []
    assert not scope["all_available_content"]


def test_retrieval_keeps_best_hit_before_a_neighbor_that_would_fill_context():
    before = paragraph(0, "Contexto anterior. " * 1000)
    best = paragraph(1, "Resultado relevante. " * 600)
    request = RetrieveRequest(user_id="user", document_id="document", source_id=SOURCE,
                              work_id=WORK, version="1", claim="Resultado relevante", chunks=[before, best])
    result = retrieve(request, Settings(_env_file=None), vector_search=lambda _: {"chunks": [best]})
    assert [item["id"] for item in result["evidence"]] == [best["id"]]
    assert result["evidence"][0]["retrieval_rank"] == 0
    assert not result["coverage_sufficient"]


def test_budget_selection_without_semantic_ranks_uses_claim_relevance():
    unrelated = paragraph(0, "Assuntos administrativos. " * 400)
    relevant = paragraph(1, "O tratamento foi associado à recuperação observada. " * 100)
    request = assessment_request([unrelated, relevant], max_tokens_budget=15000)
    fitted, _ = fit_assessment_context(request)
    assert [item["id"] for item in fitted.evidence] == [relevant["id"]]


@pytest.mark.parametrize("code", ["model_permission_blocked_org", "model_permission_blocked_project"])
def test_groq_model_permission_error_explains_required_console_change(monkeypatch, code):
    provider = AssessmentProvider(Settings(_env_file=None, llm_provider="groq", groq_api_key="test-secret"))

    def forbidden(*args, **kwargs):
        error = ProviderError("Falha do provedor api.groq.com: HTTP_403.")
        error.status_code = 403
        error.provider_code = code
        raise error

    monkeypatch.setattr(provider.http, "request", forbidden)
    result = provider.assess(assessment_request([paragraph(0)]))
    assert result.semantic_status == "INCONCLUSIVE"
    assert result.tokens_used == 0
    assert "organização ou do projeto" in result.rationale
    assert "painel Groq" in result.rationale
    assert "test-secret" not in result.model_dump_json()


@pytest.mark.parametrize(("quote", "expected_status"), [
    ("Observações clínicas", "SUPPORTED"),
    ("Observações inventadas clínicas", "INCONCLUSIVE"),
    ("Observações, clínicas", "INCONCLUSIVE"),
])
def test_pipeline_persists_only_selected_evidence_and_passes_partial_scope(monkeypatch, quote, expected_status):
    from packages.common import models as m
    from packages.common.serialization import jsonable
    from services.knowledge_service import pipeline

    chunks, rag = large_retrieval()
    # PDF lines may split a literal passage; only harmless whitespace may differ.
    for chunk in [*chunks, *rag["evidence"]]:
        chunk["text"] = chunk["text"].replace("Observações clínicas", "Observações\nclínicas")
        chunk["content_hash"] = hashlib.sha256(chunk["text"].encode()).hexdigest()
    source_id, work_id = uuid.UUID(SOURCE), uuid.UUID(WORK)
    run = SimpleNamespace(id=uuid.uuid4(), user_id="user", document_id="document", config={"mode": "real"})
    claim = SimpleNamespace(id=uuid.uuid4(), text="O tratamento foi associado à recuperação.", details={})
    occurrence = SimpleNamespace(id=uuid.uuid4(), binding_status="RESOLVED", marker="Silva (2024)")
    reference = SimpleNamespace(id=uuid.uuid4(), work_id=work_id, title="Estudo clínico",
                                authors=["Silva"], identity_status="MATCHED", access_status="FULL_TEXT")
    source = SimpleNamespace(id=source_id, work_id=work_id, version="1", details={},
                             identity_status="MATCHED", access_status="FULL_TEXT")
    # Match the UUID persistence contract while retaining the complete paragraph text.
    records = {uuid.UUID(c["id"]): SimpleNamespace(**(c | {"id": uuid.UUID(c["id"]), "source_id": source_id}))
               for c in chunks}
    scalar_answers = iter([0, None, source])
    added, calls = [], []
    db = SimpleNamespace(
        scalar=lambda _: next(scalar_answers), scalars=lambda _: list(records.values()),
        get=lambda cls, identity: records.get(identity) if cls is m.Chunk else None,
        add=added.append, commit=lambda: None, flush=lambda: None,
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings(_env_file=None, max_analysis_tokens=32000))
    monkeypatch.setattr(pipeline, "rows", lambda *args: [claim])
    monkeypatch.setattr(pipeline, "pairs", lambda *args: [(claim, occurrence, reference)])
    monkeypatch.setattr(pipeline, "ensure_active", lambda *args: None)
    monkeypatch.setattr(pipeline, "record", lambda obj: jsonable(vars(obj)))

    def call(base, path, payload, **kwargs):
        if path == "/retrieve":
            return rag
        assert path == "/assess"
        calls.append(payload)
        assert 0 < len(payload["evidence"]) < 35
        assert not payload["coverage_sufficient"]
        assert payload["scope"]["reviewed_chunks"] == len(payload["evidence"])
        assert context_token_bound(AssessmentRequest.model_validate(payload)) <= payload["max_tokens_budget"]
        return {
            "semantic_status": "SUPPORTED", "rationale": "A passagem descreve observações clínicas.",
            "evidence": [{"evidence_id": payload["evidence"][0]["id"], "quote": quote}],
            "limitations": [], "severity": "INFO", "suggested_adjustment": "Manter a delimitação.",
            "reviewed_scope": json.dumps(payload["scope"]), "tokens_used": 9000,
        }

    monkeypatch.setattr(pipeline, "call", call)
    result = pipeline.evaluate(db, run)
    assert len(calls) == 1
    persisted = [obj for obj in added if isinstance(obj, m.EvidencePassage)]
    assert len(persisted) == len(calls[0]["evidence"])
    assert {str(obj.id) for obj in persisted} == {item["id"] for item in calls[0]["evidence"]}
    assert result["tokens_used"] == 9000
    assert claim.details["token_budget_debited"] == 9000
    assert any("cobertura parcial" in warning for warning in result["warnings"])
    assessment = next(obj for obj in added if isinstance(obj, m.CitationAssessment))
    assert assessment.semantic_status == expected_status
    # Both a successful response and the conservative fallback expose valid JSON.
    scope = json.loads(assessment.reviewed_scope)
    assert scope["reviewed_chunks"] == len(persisted)
    assert scope["all_available_content"] is False
    quoted = [obj for obj in added if isinstance(obj, m.AssessmentEvidence)]
    assert len(quoted) == (1 if expected_status == "SUPPORTED" else 0)


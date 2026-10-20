"""Model adapters and deterministic post-validation of every quoted passage."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from packages.contracts.assessment import Assessment, ModelAssessment, claim_text
from packages.providers.bibliography import DEMO_TEXT, normalized
from packages.providers.http import JsonProvider, ProviderError

PROMPT_VERSION = "assessment-v2"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "services" / "llm_service" / "prompts" / f"{PROMPT_VERSION}.txt"
DECISIVE = {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_SUPPORTED_BY_REVIEWED_CONTENT"}
READABLE = {"FULL_TEXT", "PARTIAL_TEXT", "ABSTRACT_ONLY"}
MAX_CONTEXT_CHARS = 30000
MAX_COMPLETION_TOKENS = 1800
PROTOCOL_TOKEN_RESERVE = 512
CONTEXT_BUDGET_LIMITATION = "Contexto reduzido a parágrafos completos conforme o orçamento; cobertura parcial da fonte."

DEMO_ANNOTATIONS = {
    normalized("Silva (2024) observou aumento de 12% na atenção em 40 adultos após pausas de cinco minutos."): (
        "SUPPORTED", "A fonte sintética relata exatamente população, direção e tamanho do efeito citados.",
        "INFO", "Manter a delimitação a 40 adultos e o caráter observacional."
    ),
    normalized("Silva (2024) demonstrou que pausas de cinco minutos causam aumento de 12% na atenção de toda a população."): (
        "PARTIALLY_SUPPORTED", "O aumento de 12% consta da fonte, mas causalidade e generalização são expressamente limitadas.",
        "HIGH", "Em 40 adultos, pausas de cinco minutos foram associadas a aumento de 12% na atenção; o estudo não demonstra causalidade."
    ),
    normalized("Silva (2024) observou redução de 12% na atenção em 40 adultos após pausas de cinco minutos."): (
        "CONTRADICTED", "A afirmação diz redução; a fonte sintética relata aumento de 12% na atenção.",
        "HIGH", "Substituir 'redução' por 'aumento', preservando população e condições."
    ),
}


def scope_text(scope):
    return scope if isinstance(scope, str) else json.dumps(scope, ensure_ascii=False, sort_keys=True)


def model_context(request):
    """The exact messages/schema used both for budgeting and the model call."""
    payload = {
        "claim": claim_text(request.claim), "occurrence": request.occurrence,
        "reference": request.reference,
        "source": {key: request.source.get(key) for key in ("id", "work_id", "version", "origin_url", "content_hash")}
                  if request.source else {},
        "evidence": [{key: p.get(key) for key in ("id", "text", "page_start", "page_end", "source_id", "work_id", "version")}
                     for p in request.evidence],
        "scope": request.scope, "coverage_sufficient": request.coverage_sufficient,
        "identity_status": request.identity_status, "access_status": request.access_status,
    }
    messages = [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    return messages, ModelAssessment.model_json_schema()


def context_token_bound(request):
    """Preserve the conservative UTF-8 bound, including schema and output."""
    messages, schema = model_context(request)
    return (len(json.dumps({"messages": messages, "schema": schema}, ensure_ascii=False).encode("utf-8"))
            + MAX_COMPLETION_TOKENS + PROTOCOL_TOKEN_RESERVE)


def fit_assessment_context(request):
    """Select whole relevant paragraphs without raising the caller's budget."""
    def fits(candidate):
        return (sum(len(p["text"]) for p in candidate.evidence) <= MAX_CONTEXT_CHARS
                and (candidate.max_tokens_budget is None
                     or context_token_bound(candidate) <= candidate.max_tokens_budget))

    if request.mode == "demo" or not request.evidence or fits(request):
        limited = isinstance(request.scope, dict) and request.scope.get("context_budget_limited")
        return request, [CONTEXT_BUDGET_LIMITATION] if limited else []
    terms = set(re.findall(r"\w+", claim_text(request.claim).lower()))

    def relevance(item):
        index, paragraph = item
        rank = paragraph.get("retrieval_rank")
        if type(rank) is int and rank >= 0:
            return 0, rank, index
        overlap = len(terms & set(re.findall(r"\w+", paragraph["text"].lower())))
        return 1, -overlap, index

    def selected_request(indices):
        evidence = [p for index, p in enumerate(request.evidence) if index in indices]
        scope = dict(request.scope) if isinstance(request.scope, dict) else {}
        scope.setdefault("available_chunks", len(request.evidence))
        scope.update({
            "reviewed_chunks": len(evidence),
            "reviewed_pages": sorted({page for p in evidence
                                      for page in range(p["page_start"], p["page_end"] + 1)}),
            "all_available_content": False, "context_budget_limited": True,
            "note": "Somente os parágrafos selecionados dentro do orçamento foram enviados para avaliação.",
        })
        return request.model_copy(update={"evidence": evidence, "scope": scope, "coverage_sufficient": False})

    selected = set()
    for index, _ in sorted(enumerate(request.evidence), key=relevance):
        if fits(selected_request(selected | {index})):
            selected.add(index)
    fitted = selected_request(selected)
    return fitted, [CONTEXT_BUDGET_LIMITATION]


def inconclusive(request, reason, *, limitations=None, tokens=0):
    return Assessment(
        semantic_status="INCONCLUSIVE", rationale=reason, evidence=[],
        limitations=limitations or [reason], severity="INFO",
        suggested_adjustment="Revisar a identidade, obter a fonte pertinente ou ampliar a leitura antes de concluir.",
        reviewed_scope=scope_text(request.scope), tokens_used=tokens, prompt_version=PROMPT_VERSION,
    )


def canonical_quote(text: str) -> str:
    # Only harmless whitespace normalization; no lowercasing, punctuation deletion or fuzzy edits.
    return re.sub(r"\s+", " ", text).strip()


def validate_provenance(request) -> str | None:
    if request.identity_status != "MATCHED":
        return "Identidade bibliográfica não confirmada; parecer semântico inconclusivo."
    if request.access_status not in READABLE or not request.source:
        return "Conteúdo da obra não acessível para avaliação semântica."
    if not request.evidence:
        return "Nenhuma passagem verificável foi recuperada."
    source = request.source
    if source.get("identity_status", "MATCHED") != "MATCHED":
        return "A identidade do documento-fonte selecionado não foi confirmada."
    if source.get("origin", "SOURCE") != "SOURCE":
        return "O manuscrito não pode fundamentar a avaliação de si mesmo."
    required = ("id", "work_id", "version", "user_id", "document_id")
    if any(source.get(key) is None for key in required):
        return "Fonte sem proveniência suficiente para validar as evidências."
    if request.reference.get("work_id") and str(request.reference["work_id"]) != str(source["work_id"]):
        return "A fonte não corresponde à obra vinculada à referência."
    seen = set()
    for evidence in request.evidence:
        if not evidence.get("id") or evidence["id"] in seen:
            return "IDs de evidência ausentes ou duplicados."
        seen.add(evidence["id"])
        if evidence.get("origin") != "SOURCE":
            return "Passagem do manuscrito ou de origem desconhecida rejeitada."
        for evidence_key, source_key in (
            ("source_id", "id"), ("work_id", "work_id"), ("version", "version"),
            ("user_id", "user_id"), ("document_id", "document_id"),
        ):
            if str(evidence.get(evidence_key, "")) != str(source[source_key]):
                return "Passagem de outra fonte, obra, versão ou usuário rejeitada."
        if not evidence.get("text"):
            return "Passagem sem texto consultável."
    return None


def validate_assessment(raw, request, *, tokens=0):
    problem = validate_provenance(request)
    if problem:
        return inconclusive(request, problem, tokens=tokens)
    try:
        parsed = ModelAssessment.model_validate(raw)
    except ValidationError:
        return inconclusive(request, "Saída do modelo não atende ao schema de parecer.", tokens=tokens)
    by_id = {passage["id"]: passage for passage in request.evidence}
    for citation in parsed.evidence:
        passage = by_id.get(citation.evidence_id)
        if not passage or canonical_quote(citation.quote) not in canonical_quote(passage["text"]):
            return inconclusive(request, "Saída rejeitada: ID de evidência ou citação literal inexistente.", tokens=tokens)
    if parsed.semantic_status in DECISIVE and not parsed.evidence:
        return inconclusive(request, "Conclusão sem evidência explícita foi rejeitada.", tokens=tokens)
    if parsed.semantic_status == "NOT_SUPPORTED_BY_REVIEWED_CONTENT":
        if (not request.coverage_sufficient or request.access_status != "FULL_TEXT"
                or not isinstance(request.scope, dict) or not request.scope.get("all_available_content")):
            return inconclusive(request, "Cobertura insuficiente para concluir ausência de apoio.", tokens=tokens)
    limitations = parsed.limitations[:]
    if request.access_status == "ABSTRACT_ONLY":
        limitations.append("Parecer limitado ao resumo; conteúdo integral não consultado.")
    if request.access_status == "PARTIAL_TEXT":
        limitations.append("Parecer limitado ao conteúdo acessível desta versão da fonte.")
    if request.occurrence.get("secondary") or request.occurrence.get("is_secondary") or "apud" in str(request.occurrence.get("marker", "")).lower():
        limitations.append("Citação secundária: avaliação restrita à fonte intermediária efetivamente consultada.")
    return Assessment(**(parsed.model_dump() | {"limitations": list(dict.fromkeys(limitations)),
                                              "reviewed_scope": scope_text(request.scope)}),
                      tokens_used=tokens, prompt_version=PROMPT_VERSION)


class AssessmentProvider:
    def __init__(self, settings):
        self.settings = settings
        self.http = JsonProvider(timeout=settings.request_timeout)

    def assess(self, request):
        problem = validate_provenance(request)
        if problem:
            return inconclusive(request, problem)
        if request.mode == "demo":
            return self._demo(request)
        if request.source.get("is_demo"):
            return inconclusive(request, "Fonte de demonstração não pode fundamentar análise real.")
        settings = self.settings
        if settings.llm_provider == "none":
            return inconclusive(request, "Modelo de linguagem não configurado; não foi emitida confirmação semântica.")
        if len(claim_text(request.claim)) > 12000:
            return inconclusive(request, "Contexto excede o limite de avaliação; selecione passagens menores.")
        request, context_limits = fit_assessment_context(request)
        if not request.evidence:
            return inconclusive(request, "Orçamento restante de tokens insuficiente para uma chamada completa.",
                                limitations=context_limits)
        # Only the claim, occurrence, bibliographic metadata and source passages leave the service.
        # PDFs, user credentials, other documents and tools are never included.
        messages, schema = model_context(request)
        # UTF-8 bytes conservatively bound ordinary token counts. Include schema,
        # protocol overhead and the output ceiling before sending a paid request.
        conservative_tokens = context_token_bound(request)
        remaining = request.max_tokens_budget
        if remaining is not None and conservative_tokens > remaining:
            return inconclusive(request, "Orçamento restante de tokens insuficiente para uma chamada completa.")
        tokens = 0
        try:
            if settings.llm_provider in {"openai", "groq"}:
                api_key = settings.groq_api_key if settings.llm_provider == "groq" else settings.openai_api_key
                key_name = "GROQ_API_KEY" if settings.llm_provider == "groq" else "OPENAI_API_KEY"
                if not api_key.strip():
                    raise ProviderError(f"{key_name} ausente para avaliação semântica.")
                chat_payload = {
                    "model": settings.llm_model, "messages": messages, "temperature": 0,
                    "max_completion_tokens": MAX_COMPLETION_TOKENS,
                    "response_format": {"type": "json_schema", "json_schema": {
                        "name": "citation_assessment", "strict": True, "schema": schema,
                    }},
                }
                # Groq accepts the chat contract but does not support OpenAI's store field.
                if settings.llm_provider == "openai":
                    chat_payload["store"] = False
                result = self.http.request("POST", settings.llm_base_url.rstrip("/") + "/chat/completions",
                    payload=chat_payload, headers={"Authorization": f"Bearer {api_key}"}, max_attempts=1)
                choice = result["choices"][0]
                tokens = int(result.get("usage", {}).get("total_tokens", 0))
                if choice["message"].get("refusal") or choice.get("finish_reason") != "stop":
                    raise ProviderError("Modelo recusou ou não concluiu a resposta.")
                raw = json.loads(choice["message"]["content"])
            elif settings.llm_provider == "ollama":
                result = self.http.request("POST", settings.llm_base_url.rstrip("/") + "/api/chat",
                    payload={"model": settings.llm_model, "messages": messages, "stream": False,
                             "format": schema, "options": {"temperature": 0, "num_predict": MAX_COMPLETION_TOKENS}},
                    max_attempts=1)
                tokens = int(result.get("prompt_eval_count", 0)) + int(result.get("eval_count", 0))
                raw = json.loads(result["message"]["content"])
            else:
                raise ProviderError("Provedor de linguagem não suportado.")
            assessment = validate_assessment(raw, request, tokens=tokens)
            assessment.limitations = list(dict.fromkeys(assessment.limitations + context_limits))
            return assessment
        except (ProviderError, KeyError, ValueError, TypeError, IndexError) as exc:
            reason = str(exc) if isinstance(exc, ProviderError) else f"Resposta do modelo inválida: {type(exc).__name__}."
            if (settings.llm_provider == "groq" and isinstance(exc, ProviderError)
                    and getattr(exc, "status_code", None) == 403
                    and getattr(exc, "provider_code", None) in {
                        "model_permission_blocked_org", "model_permission_blocked_project",
                    }):
                reason = ("O modelo está bloqueado pelas permissões da organização ou do projeto no Groq. "
                          "Habilite o modelo configurado no painel Groq antes de iniciar uma nova análise.")
            return inconclusive(request, reason, tokens=tokens)

    def _demo(self, request):
        annotation = DEMO_ANNOTATIONS.get(normalized(claim_text(request.claim)))
        if not request.source.get("is_demo") or request.source.get("origin_url") != "fixture://citalens/source-v1":
            return inconclusive(request, "Demonstração exige fonte sintética identificada.")
        passage = next((p for p in request.evidence if canonical_quote(p["text"]) == canonical_quote(DEMO_TEXT)), None)
        if not annotation or not passage:
            return inconclusive(request, "DEMONSTRAÇÃO: este par de afirmação e fonte não possui anotação na fixture.")
        status, rationale, severity, adjustment = annotation
        result = validate_assessment({
            "semantic_status": status, "rationale": rationale,
            "evidence": [{"evidence_id": passage["id"], "quote": passage["text"]}],
            "limitations": ["DEMONSTRAÇÃO: parecer anotado manualmente para fixture sintética; não é inferência de LLM nem pesquisa real."],
            "severity": severity, "suggested_adjustment": adjustment, "reviewed_scope": scope_text(request.scope),
        }, request)
        return result.model_copy(update={"prompt_version": "demo-annotations-v1"})

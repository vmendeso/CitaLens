"""Hybrid discovery with relational provenance checks and expanded source reading."""
from __future__ import annotations

import re

import httpx

from packages.contracts.assessment import claim_text
from packages.providers.vector import validate_chunk


def source_chunk_matches(chunk: dict, request) -> bool:
    return chunk.get("origin") == "SOURCE" and all(
        str(chunk.get(key, "")) == str(getattr(request, key))
        for key in ("user_id", "document_id", "source_id", "work_id", "version")
    )


def retrieve(request, settings, vector_search=None):
    candidates = request.chunks
    for chunk in candidates:
        validate_chunk(chunk, request.user_id, request.document_id)
        if not source_chunk_matches(chunk, request):
            raise ValueError("Recuperação rejeitada: chunk não pertence à fonte, obra ou versão solicitada.")
    candidates = sorted(candidates, key=lambda c: (c.get("ordinal", 0), c["page_start"], c["offset_start"]))
    by_id = {chunk["id"]: chunk for chunk in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("IDs duplicados na fonte persistida.")
    query = claim_text(request.claim)
    terms = set(re.findall(r"\w+", query.lower()))
    ranking = sorted(candidates, key=lambda c: len(terms & set(re.findall(r"\w+", c["text"].lower()))), reverse=True)
    limitations = []
    selected_ids = []
    if vector_search is None:
        def vector_search(limit):
            with httpx.Client(timeout=settings.embedding_timeout + 45, trust_env=False) as client:
                response = client.post(settings.vector_url.rstrip("/") + "/search",
                    headers={"X-Internal-Token": settings.internal_token},
                    json={key: getattr(request, key) for key in ("user_id", "document_id", "source_id", "work_id", "version")} |
                         {"query": query, "limit": limit})
                response.raise_for_status()
                return response.json()
    try:
        for limit in (10, 30):
            result = vector_search(limit)
            limitations.extend(result.get("limitations", []))
            for chunk in result.get("chunks", []):
                stored = by_id.get(chunk.get("id"))
                if stored is None or not source_chunk_matches(chunk, request) or chunk.get("content_hash") != stored["content_hash"]:
                    limitations.append("Resultado vetorial sem proveniência SQL correspondente foi descartado.")
                    continue
                if stored["id"] not in selected_ids:
                    selected_ids.append(stored["id"])
            # All persisted source content has been located; otherwise expand before deciding.
            if len(selected_ids) == len(candidates):
                break
    except (httpx.HTTPError, ValueError, RuntimeError):
        limitations.append("Busca vetorial indisponível; recuperação lexical dos chunks persistidos utilizada.")
    for chunk in ranking:
        if chunk["id"] not in selected_ids:
            selected_ids.append(chunk["id"])
    # Read the complete available source when it fits, regardless of lexical similarity.
    max_context_chars = 24000
    selected, chars = set(), 0
    for identifier in selected_ids:
        index = next(i for i, chunk in enumerate(candidates) if chunk["id"] == identifier)
        # Every selected match includes immediate neighbours before unrelated matches.
        for neighbor in [index, *[i for i in (index - 1, index + 1) if 0 <= i < len(candidates)]]:
            chunk = candidates[neighbor]
            if chunk["id"] in selected:
                continue
            if chars + len(chunk["text"]) > max_context_chars or len(selected) >= 50:
                continue
            selected.add(chunk["id"])
            chars += len(chunk["text"])
    ranks = {identifier: rank for rank, identifier in enumerate(selected_ids)}
    evidence = [dict(chunk, retrieval_rank=ranks[chunk["id"]]) for chunk in candidates if chunk["id"] in selected]
    coverage_sufficient = bool(evidence) and len(selected) == len(candidates)
    if not coverage_sufficient:
        limitations.append("Cobertura limitada ao contexto recuperado; ausência de apoio não autoriza condenação.")
    scope = {
        "source_id": request.source_id, "work_id": request.work_id, "version": str(request.version),
        "available_chunks": len(candidates), "reviewed_chunks": len(evidence),
        "reviewed_pages": sorted({p for c in evidence for p in range(c["page_start"], c["page_end"] + 1)}),
        "expanded_search": True, "all_available_content": coverage_sufficient,
        "note": "Cobertura refere-se somente ao texto persistido desta fonte, não à integralidade da obra.",
    }
    return {"evidence": evidence, "scope": scope, "coverage_sufficient": coverage_sufficient,
            "limitations": sorted(set(limitations))}

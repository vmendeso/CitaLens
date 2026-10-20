"""Ollama's real HTTP adapter contract, using a local mock transport."""
import json

import httpx
import pytest

from packages.common.config import Settings
from packages.providers.embeddings import EmbeddingProvider
from packages.providers.http import ProviderError
from packages.providers.vector import VectorStore


def ollama_settings(**changes):
    values = {
        "embedding_provider": "ollama",
        "embedding_model": "bge-m3:567m",
        "embedding_dimensions": 1024,
        "embedding_version": "bge-m3-local-v1",
        "embedding_base_url": "http://ollama:11434/",
        "embedding_batch_size": 8,
        "embedding_timeout": 120,
        "request_timeout": 10,
        "openai_api_key": "must-not-be-sent",
        "groq_api_key": "must-not-be-sent-either",
    }
    return Settings(_env_file=None, **(values | changes))


@pytest.fixture
def ollama_http(monkeypatch):
    original_client = httpx.Client
    requests = []

    def install(handler):
        def dispatch(request):
            requests.append(request)
            return handler(request)

        transport = httpx.MockTransport(dispatch)
        monkeypatch.setattr(
            "packages.providers.http.httpx.Client",
            lambda **kwargs: original_client(transport=transport, **kwargs),
        )
        return requests

    return install


def test_ollama_preserves_multilingual_input_batch_order_without_api_credentials(ollama_http):
    texts = [
        "A atividade física melhora a saúde.",
        "Physical activity improves health.",
        "O documento trata de referências bibliográficas.",
        "Fontes primárias sustentam a afirmação.",
        "Uma evidência com pontuação: 12%, em 2024.",
        "A revisão preserva as limitações metodológicas.",
        "Clinical research reports confidence intervals.",
        "Amostra de adultos residentes no Brasil.",
        "A conclusão exige avaliação do texto integral.",
    ]

    def respond(request):
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json={
            "model": "bge-m3:567m",
            "embeddings": [[float(texts.index(text))] * 1024 for text in inputs],
        })

    requests = ollama_http(respond)
    provider = EmbeddingProvider(ollama_settings())
    provider.http.minimum_interval = 0
    vectors = provider.embed(texts)

    assert len(requests) == 2
    assert [vector[0] for vector in vectors] == list(range(len(texts)))
    assert all(len(vector) == 1024 for vector in vectors)
    sent_texts = []
    for request in requests:
        payload = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url) == "http://ollama:11434/api/embed"
        assert "Authorization" not in request.headers
        assert "must-not-be-sent" not in request.content.decode()
        assert payload["model"] == "bge-m3:567m"
        assert payload["truncate"] is False
        assert len(payload["input"]) <= 8
        assert request.extensions["timeout"]["read"] == 120
        sent_texts.extend(payload["input"])
    # BGE-M3 uses the original text for both documents and queries, without prefixes.
    assert sent_texts == texts


@pytest.mark.parametrize("response", [
    {"embeddings": []},
    {"embeddings": [[0.2] * 768]},
    {"embeddings": [[0.2] * 1024, [0.3] * 1024]},
    {"embeddings": [["invalid"] + [0.2] * 1023]},
])
def test_ollama_rejects_incompatible_or_invalid_vectors(ollama_http, response):
    ollama_http(lambda request: httpx.Response(200, json=response))
    provider = EmbeddingProvider(ollama_settings())
    with pytest.raises(ProviderError):
        provider.embed(["Texto cuja evidência deve permanecer íntegra."])


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_ollama_rejects_nonfinite_vectors(monkeypatch, number):
    provider = EmbeddingProvider(ollama_settings())
    monkeypatch.setattr(provider.http, "request", lambda *args, **kwargs: {
        "embeddings": [[number] + [0.2] * 1023],
    })
    with pytest.raises(ProviderError, match="inválido"):
        provider.embed(["A pesquisa descreve seus resultados."])


def test_ollama_context_error_does_not_retry_with_silent_truncation(ollama_http):
    requests = ollama_http(lambda request: httpx.Response(
        400, json={"error": "input length exceeds maximum context length"},
    ))
    provider = EmbeddingProvider(ollama_settings())
    with pytest.raises(ProviderError):
        provider.embed(["Uma passagem válida que o modelo recusou por seu contexto."])
    assert len(requests) == 1
    assert json.loads(requests[0].content)["truncate"] is False


@pytest.mark.parametrize("texts", [[""], ["   "], ["a" * 24001]])
def test_ollama_rejects_invalid_input_before_network(ollama_http, texts):
    def should_not_request(request):
        pytest.fail("Invalid text must be rejected before an HTTP request.")

    requests = ollama_http(should_not_request)
    provider = EmbeddingProvider(ollama_settings())
    with pytest.raises(ProviderError, match="Texto"):
        provider.embed(texts)
    assert requests == []


def test_empty_ollama_input_does_not_load_the_model(ollama_http):
    def should_not_request(request):
        pytest.fail("Empty input must not load the model.")

    requests = ollama_http(should_not_request)
    assert EmbeddingProvider(ollama_settings()).embed([]) == []
    assert requests == []


@pytest.mark.parametrize("changes", [
    {"embedding_provider": "openai"},
    {"embedding_model": "another-model"},
    {"embedding_dimensions": 768},
    {"embedding_version": "reindexed-v2"},
])
def test_embedding_changes_cannot_reuse_incompatible_index(changes):
    current = VectorStore(ollama_settings())
    changed = VectorStore(ollama_settings(**changes))
    assert current.collections["SOURCE"] != changed.collections["SOURCE"]
    assert current.collections["MANUSCRIPT"] != changed.collections["MANUSCRIPT"]
    assert current.collections == VectorStore(ollama_settings()).collections

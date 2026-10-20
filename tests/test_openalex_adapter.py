"""OpenAlex mapping, bounded calls and credential containment."""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from packages.providers.http import JsonProvider, ProviderError
from packages.providers.openalex import OpenAlexProvider, openalex_work


TITLE = "Pausas e atenção em adultos"
PDF = "https://content.openalex.org/works/W123456.pdf"


def work(**changes):
    value = {
        "id": "https://openalex.org/W123456", "doi": "https://doi.org/10.1234/PAUSAS",
        "title": TITLE, "publication_year": 2024, "type": "article",
        "authorships": [{"author": {"display_name": "Maria Silva"}}],
        "best_oa_location": {"is_oa": True, "pdf_url": "https://repository.example/best.pdf",
                             "landing_page_url": "https://repository.example/paper"},
        "locations": [{"is_oa": True, "pdf_url": "https://repository.example/other.pdf"},
                      {"is_oa": True, "pdf_url": "https://repository.example/best.pdf"},
                      {"is_oa": False, "pdf_url": "https://publisher.example/closed.pdf"}],
        "content_urls": {"pdf": PDF},
        "abstract_inverted_index": {"ABSTRACT_MUST_NOT_BECOME_EVIDENCE": [0]},
    }
    return value | changes


@pytest.fixture
def provider_http(monkeypatch):
    original = httpx.Client
    requests = []
    monkeypatch.setattr("packages.providers.http.time.sleep", lambda _: None)

    def install(handler, *, key="test-secret"):
        def dispatch(request):
            requests.append(request)
            return handler(request)
        monkeypatch.setattr("packages.providers.http.httpx.Client",
                            lambda **kwargs: original(transport=httpx.MockTransport(dispatch), **kwargs))
        http = JsonProvider(minimum_interval=0)
        return OpenAlexProvider(SimpleNamespace(openalex_api_key=key), http), requests
    return install


def test_doi_lookup_uses_bearer_and_keeps_external_id_separate(provider_http):
    provider, requests = provider_http(lambda _: httpx.Response(200, json=work()))
    result = provider.lookup({"doi": "https://doi.org/10.1234/PAUSAS", "title": TITLE}, max_attempts=1)
    assert len(requests) == provider.http.requests_used == 1
    incoming = requests[0]
    assert incoming.method == "GET"
    assert str(incoming.url) == "https://api.openalex.org/works/https://doi.org/10.1234/pausas"
    assert incoming.headers["Authorization"] == "Bearer test-secret"
    assert "test-secret" not in str(incoming.url)
    mapped = result[0]
    assert "id" not in mapped
    assert mapped["openalex_id"] == "https://openalex.org/W123456"
    assert mapped["metadata_url"] == "https://api.openalex.org/works/W123456"
    assert mapped["metadata_origin"] == "OpenAlex"
    assert mapped["doi"] == "10.1234/pausas"
    assert mapped["authors"] == [{"name": "Maria Silva"}]
    assert mapped["year"] == 2024
    assert mapped["content_pdf_url"] == PDF
    assert "ABSTRACT_MUST_NOT_BECOME_EVIDENCE" not in str(mapped)


def test_title_search_is_bounded_and_omits_private_reference_fields(provider_http):
    provider, requests = provider_http(lambda _: httpx.Response(200, json={"results": [work()]}), key="")
    reference = {"title": TITLE, "manuscript_text": "PRIVATE_TEXT", "user_id": "PRIVATE_USER"}
    provider.lookup(reference)
    incoming = requests[0]
    assert incoming.url.path == "/works"
    assert dict(incoming.url.params) == {"search.title": TITLE, "per_page": "5"}
    assert "Authorization" not in incoming.headers
    assert "PRIVATE_" not in str(incoming.url)
    assert not incoming.content


@pytest.mark.parametrize(("title", "expected"), [
    ("What is fraudgpt?", "What is fraudgpt"),
    ('A* methods: "AND" or NOT (robots?)~2', "A methods and or not robots 2"),
    ("Atenção: revisão de literatura?\n\nResultados", "Atenção revisão de literatura Resultados"),
])
def test_title_punctuation_is_text_not_openalex_query_syntax(provider_http, title, expected):
    def handler(request):
        query = request.url.params["search.title"]
        if any(char in query for char in '*?~"()'):
            return httpx.Response(400, json={"error": "Wildcards require exact search"})
        return httpx.Response(200, json={"results": [work(title=title)]})

    provider, requests = provider_http(handler)
    reference = {"title": title}
    result = provider.lookup(reference)
    assert requests[0].url.params["search.title"] == expected
    assert result[0]["title"] == reference["title"] == title
    assert len(requests) == provider.http.requests_used == 1


def test_title_without_searchable_words_never_spends_a_request(provider_http):
    provider, requests = provider_http(lambda _: pytest.fail("Must not call the API"))
    with pytest.raises(ProviderError):
        provider.lookup({"title": "? * () \" ~"})
    assert requests == []


def test_same_lookup_shares_json_cache_without_charging_network_twice(provider_http):
    provider, requests = provider_http(lambda _: httpx.Response(200, json={"results": [work()]}))
    first = provider.lookup({"title": TITLE})
    first[0]["title"] = "Changed locally"
    second = provider.lookup({"title": TITLE})
    assert len(requests) == 1
    assert provider.http.requests_used == 0
    assert second[0]["title"] == TITLE


def test_only_open_pdf_locations_are_prioritized_and_deduplicated():
    mapped = openalex_work(work())
    assert mapped["public_pdf_urls"] == ["https://repository.example/best.pdf", "https://repository.example/other.pdf"]


@pytest.mark.parametrize("advertised", [
    "https://evil.example/works/W123456.pdf",
    "https://content.openalex.org.evil.example/works/W123456.pdf",
    "https://content.openalex.org/works/W999.pdf",
    "http://content.openalex.org/works/W123456.pdf",
    PDF + "?api_key=untrusted",
    PDF + "#fragment",
    None,
])
def test_authenticated_content_url_is_only_exact_canonical_advertised_url(advertised):
    mapped = openalex_work(work(content_urls={"pdf": advertised}))
    assert mapped["content_pdf_url"] is None


def test_credentials_and_openalex_content_do_not_enter_public_download_candidates():
    value = work(best_oa_location=None, locations=[
        {"is_oa": True, "pdf_url": "https://user:secret@example.org/paper.pdf"},
        {"is_oa": True, "pdf_url": PDF},
        {"is_oa": True, "pdf_url": "file:///private/paper.pdf"},
        {"is_oa": "true", "pdf_url": "https://publisher.example/not-explicitly-open.pdf"},
    ])
    assert openalex_work(value)["public_pdf_urls"] == []


@pytest.mark.parametrize("changes", [
    {"id": "https://evil.example/W123456"}, {"id": "W123456/evil"}, {"id": "secret"},
    {"title": []}, {"publication_year": True}, {"authorships": {}},
    {"authorships": [{"author": "secret"}]}, {"locations": {}},
    {"locations": ["secret"]}, {"content_urls": {"pdf": {"secret": "secret"}}},
])
def test_malformed_work_schema_fails_closed_without_echoing_provider_values(changes):
    with pytest.raises(ProviderError) as caught:
        openalex_work(work(**changes))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("payload", [[], {}, {"results": None}, {"results": {}}, {"results": [42]}])
def test_malformed_search_envelope_consumes_only_actual_request(provider_http, payload):
    provider, requests = provider_http(lambda _: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        provider.lookup({"title": TITLE}, max_attempts=2)
    assert len(requests) == provider.http.requests_used == 1


def test_retry_count_respects_remaining_budget_without_disclosing_errors(provider_http):
    provider, requests = provider_http(lambda _: httpx.Response(503, text="test-secret in response"))
    with pytest.raises(ProviderError) as caught:
        provider.lookup({"title": TITLE}, max_attempts=2)
    assert len(requests) == provider.http.requests_used == 2
    assert "test-secret" not in str(caught.value)


def test_empty_reference_or_exhausted_budget_never_make_requests(provider_http):
    provider, requests = provider_http(lambda _: pytest.fail("Must not call the API"))
    with pytest.raises(ProviderError):
        provider.lookup({"authors": ["Silva"]})
    with pytest.raises(ProviderError):
        provider.lookup({"title": TITLE}, max_attempts=0)
    assert requests == []


def test_normalization_does_not_mutate_provider_payload():
    original = work()
    before = deepcopy(original)
    openalex_work(original)
    assert original == before

"""SearXNG discovery uses bibliography metadata; result snippets are never evidence."""
import json
import socket

import httpx
import pytest

from packages.common.config import Settings
from packages.contracts.assessment import ResolveRequest
from packages.providers.bibliography import SourceResolver
from packages.providers.http import Download


TITLE = "Pausas e atenção em adultos"
SNIPPET = "SNIPPET_MUST_NEVER_BECOME_EVIDENCE"


def settings(**changes):
    values = {
        "openalex_enabled": False,
        "web_provider": "searxng", "searxng_base_url": "http://searxng:8080/",
        "brave_api_key": "paid-key-must-not-be-sent", "openai_api_key": "another-secret",
        "max_external_queries": 10,
    }
    return Settings(_env_file=None, **(values | changes))


def request(**changes):
    values = {
        "reference": {"title": TITLE, "authors": ["Silva"], "year": 2024,
                      "manuscript_text": "PRIVATE_MANUSCRIPT_MUST_NOT_BE_QUERIED"},
        "user_id": "private-user-id", "document_id": "private-document-id",
    }
    return ResolveRequest(**(values | changes))


def crossref(matched=False):
    return {"message": {"items": [{
        "DOI": "10.1234/pausas", "title": [TITLE], "author": [{"family": "Silva"}],
        "published": {"date-parts": [[2024]]},
    }] if matched else []}}


@pytest.fixture
def web_http(monkeypatch):
    original_client = httpx.Client
    requests = []
    monkeypatch.setattr("packages.providers.http.time.sleep", lambda duration: None)

    def install(searxng_handler, *, matched=True, crossref_payload=None):
        def dispatch(incoming):
            requests.append(incoming)
            if incoming.url.host == "api.crossref.org":
                return httpx.Response(200, json=crossref_payload or crossref(matched))
            assert incoming.url.host == "searxng", "Unexpected external service or paid-provider fallback."
            return searxng_handler(incoming)

        transport = httpx.MockTransport(dispatch)
        monkeypatch.setattr("packages.providers.http.httpx.Client",
                            lambda **kwargs: original_client(transport=transport, **kwargs))
        return requests

    return install


def test_searxng_queries_only_bibliography_and_needs_no_external_key(web_http):
    requests = web_http(lambda incoming: httpx.Response(200, json={"results": [{
        "url": "https://publisher.example/paper", "title": TITLE, "content": SNIPPET,
    }]}))
    resolver = SourceResolver(settings(brave_api_key=""))
    resolver.http.minimum_interval = 0
    result = resolver.resolve(request(max_external_queries=2))

    assert len(requests) == 2
    search = requests[1]
    assert search.method == "GET"
    assert search.url.path == "/search"
    assert search.url.params["q"] == f"{TITLE} silva 2024"
    assert search.url.params["format"] == "json"
    assert search.url.params["categories"] == "general"
    assert search.url.params["safesearch"] == "1"
    assert "pageno" not in search.url.params
    assert search.headers["Accept"] == "application/json"
    assert "Authorization" not in search.headers
    assert "X-Subscription-Token" not in search.headers
    assert "PRIVATE_MANUSCRIPT" not in str(search.url)
    assert "private-user-id" not in str(search.url)
    assert "private-document-id" not in str(search.url)
    assert result["external_queries"] == 2
    assert result["identity_status"] == "MATCHED"
    assert result["source"] is None
    assert result["attempts"][-1]["provider"] == "searxng"
    assert result["attempts"][-1]["result"] == "discovery_only"
    assert SNIPPET not in json.dumps(result)


def test_searxng_candidate_is_downloaded_and_validated_before_becoming_evidence(web_http, monkeypatch):
    url = "https://publisher.example/paper"
    web_http(lambda incoming: httpx.Response(200, json={"results": [
        {"url": url, "content": SNIPPET}, {"url": url, "content": "duplicate"},
    ]}), matched=True)
    downloaded_urls = []
    html = (
        f'<meta name="citation_title" content="{TITLE}">'
        '<meta name="citation_doi" content="10.1234/pausas">'
        '<meta name="citation_author" content="Silva">'
        '<meta name="citation_publication_date" content="2024">'
        '<meta name="citation_abstract" content="O resumo original descreve os resultados do estudo.">'
    ).encode()

    def download(found_url, **kwargs):
        downloaded_urls.append(found_url)
        kwargs["on_request"]()
        return Download(html, found_url, "text/html", 200)

    monkeypatch.setattr("packages.providers.bibliography.download_public", download)
    resolver = SourceResolver(settings())
    monkeypatch.setattr(resolver, "_preserve", lambda *args: None)
    resolver.http.minimum_interval = 0
    result = resolver.resolve(request())

    assert downloaded_urls == [url]
    assert result["external_queries"] == 3
    assert result["identity_status"] == "MATCHED"
    assert result["access_status"] == "ABSTRACT_ONLY"
    assert result["source"]["pages"][0]["normalized_text"] == "O resumo original descreve os resultados do estudo."
    assert SNIPPET not in json.dumps(result)


def test_unresolved_identity_does_not_search_web_or_download(web_http, monkeypatch):
    requests = web_http(lambda _: pytest.fail("Unconfirmed references must not search."), matched=False)
    monkeypatch.setattr("packages.providers.bibliography.download_public",
                        lambda *args, **kwargs: pytest.fail("Unconfirmed references must not download."))
    result = SourceResolver(settings()).resolve(request())
    assert len(requests) == result["external_queries"] == 1
    assert result["identity_status"] == "UNRESOLVED"
    assert result["source"] is None
    assert [attempt["provider"] for attempt in result["attempts"]] == ["crossref"]
    assert any("identidade bibliográfica" in limitation for limitation in result["limitations"])


def test_conflicting_doi_metadata_does_not_search_web_or_download(web_http, monkeypatch):
    metadata = crossref(matched=True)["message"]["items"][0] | {"title": ["An unrelated work"]}
    requests = web_http(lambda _: pytest.fail("Conflicting references must not search."),
                        crossref_payload={"message": metadata})
    monkeypatch.setattr("packages.providers.bibliography.download_public",
                        lambda *args, **kwargs: pytest.fail("Conflicting references must not download."))
    reference = request().reference | {"doi": "10.1234/pausas"}
    result = SourceResolver(settings()).resolve(request(reference=reference))
    assert len(requests) == result["external_queries"] == 1
    assert result["identity_status"] == "METADATA_MISMATCH"
    assert result["source"] is None
    assert [attempt["provider"] for attempt in result["attempts"]] == ["crossref"]
    assert any("identidade bibliográfica" in limitation for limitation in result["limitations"])


def test_searxng_cannot_override_a_conflicting_download_identity(web_http, monkeypatch):
    web_http(lambda incoming: httpx.Response(200, json={"results": [{
        "url": "https://publisher.example/wrong-paper", "title": TITLE, "content": SNIPPET,
    }]}), matched=True)

    def download(url, **kwargs):
        kwargs["on_request"]()
        html = '<meta name="citation_title" content="An unrelated work"><meta name="citation_author" content="Smith">'
        return Download(html.encode(), url, "text/html", 200)

    monkeypatch.setattr("packages.providers.bibliography.download_public", download)
    result = SourceResolver(settings()).resolve(request())
    assert result["source"] is None
    assert result["access_status"] == "FETCH_ERROR"
    assert result["attempts"][-1]["result"] == "failed"
    assert SNIPPET not in json.dumps(result)


@pytest.mark.parametrize("failure", ["network", "http503", "invalid_json"])
def test_searxng_failure_uses_only_remaining_budget_and_exposes_no_response_body(web_http, failure):
    def fail(incoming):
        if failure == "network":
            raise httpx.ConnectError("SENSITIVE_PROVIDER_DETAIL", request=incoming)
        if failure == "http503":
            return httpx.Response(503, text="SENSITIVE_PROVIDER_DETAIL")
        return httpx.Response(200, text="SENSITIVE_PROVIDER_DETAIL")

    requests = web_http(fail)
    resolver = SourceResolver(settings())
    resolver.http.minimum_interval = 0
    result = resolver.resolve(request(max_external_queries=3))

    expected = 2 if failure == "invalid_json" else 3
    assert len(requests) == expected
    assert result["external_queries"] == expected
    assert result["source"] is None
    assert result["attempts"][-1]["provider"] == "searxng"
    assert result["attempts"][-1]["result"] == "error"
    assert "SENSITIVE_PROVIDER_DETAIL" not in json.dumps(result)
    assert "paid-key-must-not-be-sent" not in json.dumps(result)


@pytest.mark.parametrize("response", [[], {}, {"results": None}, {"results": {"url": "https://example.org"}}])
def test_invalid_searxng_payload_fails_closed_without_double_counting(web_http, response):
    requests = web_http(lambda incoming: httpx.Response(200, json=response))
    result = SourceResolver(settings()).resolve(request(max_external_queries=3))
    assert len(requests) == result["external_queries"] == 2
    assert result["source"] is None
    assert result["attempts"][-1]["result"] == "error"
    assert any("inválida" in limitation for limitation in result["limitations"])


def test_exhausted_budget_does_not_call_searxng(web_http):
    requests = web_http(lambda incoming: pytest.fail("No budget remains for web discovery."))
    result = SourceResolver(settings()).resolve(request(max_external_queries=1))
    assert len(requests) == result["external_queries"] == 1
    assert all(attempt["provider"] != "searxng" for attempt in result["attempts"])
    assert any("Limite" in limitation for limitation in result["limitations"])


@pytest.mark.parametrize("provider, expected", [("none", "desativada"), ("unsupported", "não suportado")])
def test_disabled_or_unknown_web_provider_is_explicit_without_paid_fallback(web_http, provider, expected):
    requests = web_http(lambda incoming: pytest.fail("A disabled provider must not search."))
    result = SourceResolver(settings(web_provider=provider)).resolve(request())
    assert len(requests) == result["external_queries"] == 1
    assert any(expected in limitation for limitation in result["limitations"])
    assert result["source"] is None


def test_searxng_cache_hit_does_not_debit_another_external_request(web_http):
    requests = web_http(lambda incoming: httpx.Response(200, json={"results": []}))
    resolver = SourceResolver(settings())
    first = resolver.resolve(request())
    second = resolver.resolve(request())
    assert len(requests) == 2
    assert first["external_queries"] == 2
    assert second["external_queries"] == 0


def test_searxng_unavailable_engines_are_reported_without_trusting_snippets(web_http):
    web_http(lambda incoming: httpx.Response(200, json={"results": [], "unresponsive_engines": [["engine", "timeout"]]}))
    result = SourceResolver(settings()).resolve(request())
    assert any("mecanismos indisponíveis" in limitation for limitation in result["limitations"])
    assert result["source"] is None


def test_searxng_urls_still_pass_public_download_ssrf_validation(web_http, monkeypatch):
    web_http(lambda incoming: httpx.Response(200, json={"results": [{"url": "http://127.0.0.1/private"}]}), matched=True)
    monkeypatch.setattr("packages.providers.http.socket.getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ])
    monkeypatch.setattr("packages.providers.http.http.client.HTTPConnection",
                        lambda *args, **kwargs: pytest.fail("Search result must not contact a private address."))
    result = SourceResolver(settings()).resolve(request())
    assert result["source"] is None
    assert result["external_queries"] == 2
    assert result["attempts"][-1]["result"] == "failed"
    assert "bloqueado" in result["attempts"][-1]["error"]


def test_brave_remains_an_explicit_optional_provider(monkeypatch):
    original_client = httpx.Client
    requests = []

    def dispatch(incoming):
        requests.append(incoming)
        if incoming.url.host == "api.crossref.org":
            return httpx.Response(200, json=crossref(matched=True))
        assert incoming.url.host == "api.search.brave.com"
        assert incoming.headers["X-Subscription-Token"] == "explicit-brave-key"
        return httpx.Response(200, json={"web": {"results": [{"url": "https://publisher.example/paper"}]}})

    transport = httpx.MockTransport(dispatch)
    monkeypatch.setattr("packages.providers.http.httpx.Client",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    result = SourceResolver(settings(web_provider="brave", brave_api_key="explicit-brave-key")).resolve(
        request(max_external_queries=2))
    assert len(requests) == result["external_queries"] == 2
    assert result["attempts"][-1]["provider"] == "brave"
    assert result["source"] is None

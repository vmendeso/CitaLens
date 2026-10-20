"""Academic discovery must preserve identity, request budgets and credentials."""
import json
import socket
from types import SimpleNamespace

import fitz
import httpx
import pytest

from packages.common.config import Settings
from packages.contracts.assessment import ResolveRequest
from packages.providers.bibliography import DEMO_DOI, DEMO_TITLE, SourceResolver
from packages.providers.http import Download, JsonProvider, ProviderError, UnsafeURL, download_public


TITLE = "The effects of rest breaks on adult attention"
DOI = "10.1234/rest-breaks"
PUBLIC_PDF = "https://repository.example/paper.pdf"
CONTENT_PDF = "https://content.openalex.org/works/W123456.pdf"
KEY = "test-openalex-secret-do-not-expose"


def settings(**changes):
    values = {
        "openalex_enabled": True, "openalex_api_key": KEY,
        "web_provider": "none", "max_external_queries": 10,
        "brave_api_key": "unrelated-brave-secret",
    }
    return Settings(_env_file=None, **(values | changes))


def reference(**changes):
    return {"title": TITLE, "authors": ["Silva"], "year": 2024, "doi": DOI} | changes


def request(**changes):
    values = {"reference": reference(), "user_id": "u1", "document_id": "d1"}
    return ResolveRequest(**(values | changes))


def crossref_work(**changes):
    return {
        "DOI": DOI, "title": [TITLE], "author": [{"family": "Silva"}],
        "published": {"date-parts": [[2024]]},
    } | changes


def openalex_work(**changes):
    return {
        "id": "https://openalex.org/W123456", "doi": "https://doi.org/" + DOI,
        "title": TITLE, "display_name": TITLE, "publication_year": 2024,
        "authorships": [{"author": {"display_name": "Marina Silva"}}],
        "type": "article",
        "best_oa_location": {"is_oa": True, "pdf_url": PUBLIC_PDF},
        "primary_location": {"is_oa": True, "pdf_url": PUBLIC_PDF},
        "locations": [{"is_oa": True, "pdf_url": PUBLIC_PDF}],
        "content_urls": {"pdf": CONTENT_PDF},
    } | changes


@pytest.fixture
def provider_http(monkeypatch):
    """All external metadata and web requests are intercepted; no network is used."""
    original_client = httpx.Client
    requests = []
    monkeypatch.setattr("packages.providers.http.time.sleep", lambda seconds: None)

    def install(*, crossref=None, openalex=None, crossref_status=200,
                openalex_status=200, web_urls=()):
        crossref = crossref_work() if crossref is None else crossref
        openalex = openalex_work() if openalex is None else openalex

        def dispatch(incoming):
            requests.append(incoming)
            if incoming.url.host == "api.crossref.org":
                payload = ({"items": [crossref] if crossref else []}
                           if incoming.url.path == "/works" else crossref)
                return httpx.Response(crossref_status, json={"message": payload})
            if incoming.url.host == "api.openalex.org":
                if openalex_status != 200:
                    return httpx.Response(openalex_status, text="PRIVATE_PROVIDER_BODY " + KEY)
                payload = ({"results": [openalex] if openalex else []}
                           if incoming.url.path == "/works" else openalex)
                return httpx.Response(200, json=payload)
            assert incoming.url.host == "searxng", "Unexpected external service."
            return httpx.Response(200, json={"results": [{
                "url": url, "content": "UNTRUSTED_SEARCH_SNIPPET",
            } for url in web_urls]})

        transport = httpx.MockTransport(dispatch)
        monkeypatch.setattr("packages.providers.http.httpx.Client",
                            lambda **kwargs: original_client(transport=transport, **kwargs))
        return requests

    return install


@pytest.fixture
def pdf_bytes():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 50), TITLE, fontsize=12)
        page.insert_text((40, 75), "Marina Silva - 2024", fontsize=11)
        page.insert_textbox(fitz.Rect(40, 110, 550, 250),
                            "The study observed adult attention after scheduled rest breaks. "
                            "The results describe an association and do not establish causation.",
                            fontsize=11)
        return pdf.tobytes()


@pytest.fixture
def downloaded_pdf(monkeypatch, pdf_bytes):
    calls = []

    def download(url, **kwargs):
        calls.append((url, kwargs))
        kwargs["on_request"]()
        return Download(pdf_bytes, url, "application/pdf", 200)

    monkeypatch.setattr("packages.providers.bibliography.download_public", download)
    monkeypatch.setattr(SourceResolver, "_preserve", lambda *args: None)
    return calls


@pytest.mark.parametrize("has_doi", [True, False])
def test_openalex_finds_public_pdf_by_doi_or_title_and_validates_content(
        provider_http, downloaded_pdf, has_doi):
    requests = provider_http()
    result = SourceResolver(settings()).resolve(request(reference=reference(doi=DOI if has_doi else "")))

    assert [incoming.url.host for incoming in requests] == ["api.crossref.org", "api.openalex.org"]
    assert requests[1].headers["Authorization"] == "Bearer " + KEY
    assert "Authorization" not in requests[0].headers
    assert downloaded_pdf[0][0] == PUBLIC_PDF
    assert not downloaded_pdf[0][1].get("openalex_api_key")
    assert result["identity_status"] == "MATCHED"
    assert result["access_status"] == "PARTIAL_TEXT"
    assert result["source"]["origin_url"] == PUBLIC_PDF
    assert "do not establish causation" in result["source"]["pages"][0]["normalized_text"]
    assert result["external_queries"] == 3
    assert KEY not in json.dumps(result)


def test_public_pdf_is_tried_before_metered_openalex_content(provider_http, downloaded_pdf):
    provider_http()
    result = SourceResolver(settings(web_provider="searxng")).resolve(request())
    assert result["source"]
    assert [url for url, kwargs in downloaded_pdf] == [PUBLIC_PDF]
    assert all(attempt["provider"] != "searxng" for attempt in result["attempts"])


def test_openalex_searches_title_when_crossref_has_no_match_or_doi(provider_http, downloaded_pdf):
    requests = provider_http(crossref={})
    result = SourceResolver(settings()).resolve(request(reference=reference(
        doi="", manuscript_text="PRIVATE_MANUSCRIPT_CONTENT")))
    assert requests[1].url.path == "/works"
    assert requests[1].url.params["search.title"] == TITLE
    assert "PRIVATE_MANUSCRIPT_CONTENT" not in str(requests[1].url)
    assert result["work"]["metadata_origin"] == "OpenAlex"
    assert result["source"]["origin_url"] == PUBLIC_PDF
    assert result["external_queries"] == 3


def test_anonymous_openalex_can_find_public_pdf_without_trying_metered_content(provider_http, downloaded_pdf):
    requests = provider_http()
    result = SourceResolver(settings(openalex_api_key="")).resolve(request())
    assert "Authorization" not in requests[1].headers
    assert [url for url, kwargs in downloaded_pdf] == [PUBLIC_PDF]
    assert result["source"]["origin_url"] == PUBLIC_PDF


def test_openalex_cached_pdf_is_not_requested_without_a_key(provider_http, monkeypatch):
    provider_http(openalex=openalex_work(best_oa_location=None, primary_location=None, locations=[]))
    monkeypatch.setattr("packages.providers.bibliography.download_public",
                        lambda *args, **kwargs: pytest.fail("Cached content requires a key."))
    result = SourceResolver(settings(openalex_api_key="")).resolve(request())
    assert result["source"] is None
    assert result["external_queries"] == 2
    assert any("OPENALEX_API_KEY" in limitation for limitation in result["limitations"])


@pytest.mark.parametrize("crossref_status, expected_queries", [(404, 3), (503, 5)])
def test_openalex_recovers_when_crossref_is_unavailable(
        provider_http, downloaded_pdf, crossref_status, expected_queries):
    requests = provider_http(crossref_status=crossref_status)
    result = SourceResolver(settings()).resolve(request())
    assert result["identity_status"] == "MATCHED"
    assert result["source"]["origin_url"] == PUBLIC_PDF
    assert "OpenAlex" in result["work"]["metadata_origin"]
    assert result["external_queries"] == expected_queries == len(requests) + 1
    assert any("Crossref" in limitation for limitation in result["limitations"])


@pytest.mark.parametrize("conflict", [
    {"doi": "https://doi.org/10.9999/another-paper"},
    {"publication_year": 1998},
    {"authorships": [{"author": {"display_name": "Jane Smith"}}]},
    {"title": "Neutrinos in the early universe", "display_name": "Neutrinos in the early universe"},
])
def test_conflicting_openalex_work_cannot_supply_links_for_crossref_work(
        provider_http, monkeypatch, conflict):
    provider_http(openalex=openalex_work(**conflict))
    monkeypatch.setattr("packages.providers.bibliography.download_public",
                        lambda *args, **kwargs: pytest.fail("Conflicting identity must not supply PDF candidates."))
    result = SourceResolver(settings()).resolve(request())
    assert result["identity_status"] == "MATCHED"
    assert result["work"]["doi"] == DOI
    assert result["source"] is None
    assert result["external_queries"] == 2


def test_reference_without_doi_still_rejects_conflict_between_metadata_providers(provider_http, monkeypatch):
    provider_http(openalex=openalex_work(doi="https://doi.org/10.9999/another-paper"))
    monkeypatch.setattr("packages.providers.bibliography.download_public",
                        lambda *args, **kwargs: pytest.fail("OpenAlex DOI conflicts with already identified Crossref work."))
    result = SourceResolver(settings()).resolve(request(reference=reference(doi="")))
    assert result["work"]["doi"] == DOI
    assert result["source"] is None


@pytest.mark.parametrize("status, expected_queries", [(401, 4), (429, 6)])
def test_openalex_auth_or_rate_limit_error_falls_back_without_secret_or_body_leak(
        provider_http, downloaded_pdf, status, expected_queries):
    requests = provider_http(openalex_status=status, web_urls=[PUBLIC_PDF])
    result = SourceResolver(settings(web_provider="searxng")).resolve(request(max_external_queries=6))
    assert result["source"]["origin_url"] == PUBLIC_PDF
    assert result["external_queries"] == expected_queries == len(requests) + 1
    assert any(incoming.url.host == "searxng" for incoming in requests)
    for incoming in requests:
        if incoming.url.host != "api.openalex.org":
            assert "Authorization" not in incoming.headers
        assert KEY not in str(incoming.url)
    serialized = json.dumps(result)
    assert KEY not in serialized
    assert "PRIVATE_PROVIDER_BODY" not in serialized
    assert "UNTRUSTED_SEARCH_SNIPPET" not in serialized


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_request_budget_bounds_metadata_and_pdf_acquisition(provider_http, downloaded_pdf, budget):
    requests = provider_http()
    result = SourceResolver(settings()).resolve(request(max_external_queries=budget))
    assert result["external_queries"] == len(requests) + len(downloaded_pdf) == budget
    assert len(downloaded_pdf) == (1 if budget == 3 else 0)
    assert bool(result["source"]) == (budget == 3)


def test_demo_does_not_contact_openalex_or_download(monkeypatch):
    resolver = SourceResolver(settings())
    monkeypatch.setattr(resolver.http, "request", lambda *args, **kwargs: pytest.fail("Demo must stay local."))
    monkeypatch.setattr("packages.providers.bibliography.download_public",
                        lambda *args, **kwargs: pytest.fail("Demo must not download."))
    result = resolver.resolve(request(mode="demo", reference=reference(title=DEMO_TITLE, doi=DEMO_DOI)))
    assert result["external_queries"] == 0
    assert result["source"]["is_demo"] is True


def test_openalex_cache_pdf_receives_key_only_after_public_pdf_failure(provider_http, monkeypatch, pdf_bytes):
    provider_http()
    calls = []

    def download(url, **kwargs):
        calls.append((url, kwargs.get("openalex_api_key")))
        kwargs["on_request"]()
        if url == PUBLIC_PDF:
            raise ProviderError("HTTP_404")
        return Download(pdf_bytes, url, "application/pdf", 200)

    monkeypatch.setattr("packages.providers.bibliography.download_public", download)
    monkeypatch.setattr(SourceResolver, "_preserve", lambda *args: None)
    result = SourceResolver(settings()).resolve(request())
    assert calls == [(PUBLIC_PDF, None), (CONTENT_PDF, KEY)]
    assert result["external_queries"] == 4
    assert result["source"]["origin_url"] == CONTENT_PDF
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("budget, recovered", [(4, False), (6, True)])
def test_web_fallback_runs_after_academic_downloads_fail_if_budget_remains(
        provider_http, monkeypatch, pdf_bytes, budget, recovered):
    fallback_pdf = "https://another-repository.example/recovered.pdf"
    requests = provider_http(web_urls=[fallback_pdf])
    downloads = []

    def download(url, **kwargs):
        downloads.append(url)
        kwargs["on_request"]()
        if url in {PUBLIC_PDF, CONTENT_PDF}:
            assert requests[-1].url.host == "api.openalex.org"
            raise ProviderError("HTTP_404" if url == PUBLIC_PDF else "ACCESS_RESTRICTED")
        assert url == fallback_pdf
        assert requests[-1].url.host == "searxng"
        assert not kwargs.get("openalex_api_key")
        return Download(pdf_bytes, url, "application/pdf", 200)

    monkeypatch.setattr("packages.providers.bibliography.download_public", download)
    monkeypatch.setattr(SourceResolver, "_preserve", lambda *args: None)
    result = SourceResolver(settings(web_provider="searxng")).resolve(request(max_external_queries=budget))
    assert downloads == [PUBLIC_PDF, CONTENT_PDF] + ([fallback_pdf] if recovered else [])
    assert result["external_queries"] == len(requests) + len(downloads) == budget
    assert bool(result["source"]) is recovered
    assert KEY not in json.dumps(result)
    if recovered:
        assert result["source"]["origin_url"] == fallback_pdf


@pytest.mark.parametrize("malformed", [
    [], {}, {"id": "https://openalex.org/W123456", "title": TITLE, "authorships": "private-value"},
    openalex_work(publication_year="private-value"),
    openalex_work(locations={"pdf_url": "private-value"}),
])
def test_malformed_openalex_metadata_keeps_crossref_identity_and_allows_web_fallback(
        provider_http, downloaded_pdf, malformed):
    requests = provider_http(openalex=malformed, web_urls=[PUBLIC_PDF])
    result = SourceResolver(settings(web_provider="searxng")).resolve(request())
    assert result["identity_status"] == "MATCHED"
    assert result["source"]["origin_url"] == PUBLIC_PDF
    assert result["work"]["metadata_origin"] == "Crossref REST API"
    assert result["external_queries"] == len(requests) + len(downloaded_pdf) == 4
    assert any(attempt["provider"] == "openalex" and attempt["result"] == "error"
               for attempt in result["attempts"])
    assert "private-value" not in json.dumps(result)
    assert KEY not in json.dumps(result)


def test_invalid_lookup_input_does_not_charge_previous_crossref_request_again(provider_http):
    requests = provider_http()
    result = SourceResolver(settings()).resolve(request(reference=reference(title=[TITLE])))
    assert len(requests) == result["external_queries"] == 1
    assert result["source"] is None


@pytest.fixture
def pinned_downloads(monkeypatch):
    """Exercise actual downloader headers and redirects without DNS or sockets."""
    from packages.providers import http
    requests = []
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])

    def install(*, redirect=True):
        class Connection:
            def __init__(self, host, ip, port, timeout):
                self.host = host
                self.sock = SimpleNamespace(shutdown=lambda how: None, close=lambda: None,
                                            settimeout=lambda timeout: None)

            def connect(self):
                pass

            def request(self, method, path, headers):
                requests.append({"host": self.host, "path": path, "headers": headers})

            def getresponse(self):
                if redirect and self.host == "content.openalex.org":
                    return SimpleNamespace(status=302, getheader=lambda name, default=None: PUBLIC_PDF)
                pieces = iter([b"%PDF-test-bytes", b""])
                return SimpleNamespace(
                    status=200,
                    getheader=lambda name, default=None: {"Content-Type": "application/pdf"}.get(name, default),
                    read1=lambda size: next(pieces),
                )

            def close(self):
                pass

        monkeypatch.setattr(http, "_PinnedHTTPS", Connection)
        return requests

    return install


def test_authenticated_openalex_download_strips_auth_on_redirect_and_counts_each_request(pinned_downloads):
    requests = pinned_downloads()
    charged = []
    result = download_public(CONTENT_PDF, openalex_api_key=KEY,
                             on_request=lambda: charged.append(1))
    assert result.final_url == PUBLIC_PDF
    assert len(charged) == len(requests) == 2
    assert requests[0]["headers"]["Authorization"] == "Bearer " + KEY
    assert "Authorization" not in requests[1]["headers"]
    assert all(KEY not in item["path"] for item in requests)


def test_public_download_never_receives_authorization(pinned_downloads):
    requests = pinned_downloads(redirect=False)
    download_public(PUBLIC_PDF)
    assert len(requests) == 1
    assert "Authorization" not in requests[0]["headers"]


@pytest.mark.parametrize("url", [
    PUBLIC_PDF,
    "http://content.openalex.org/works/W123456.pdf",
    "https://content.openalex.org/works/W123456.pdf?api_key=other-secret",
    "https://content.openalex.org.evil.example/works/W123456.pdf",
    "https://content.openalex.org/works/not-a-work.pdf",
])
def test_openalex_key_cannot_be_sent_to_arbitrary_download_url(pinned_downloads, url):
    requests = pinned_downloads()
    with pytest.raises(UnsafeURL):
        download_public(url, openalex_api_key=KEY)
    assert requests == []


def test_redirect_limit_charges_only_requests_actually_sent(pinned_downloads):
    requests = pinned_downloads()
    charged = []
    with pytest.raises(ProviderError, match="Redirecionamentos"):
        download_public(CONTENT_PDF, openalex_api_key=KEY, max_redirects=0,
                        on_request=lambda: charged.append(1))
    assert len(requests) == len(charged) == 1


def test_anonymous_metadata_cache_cannot_hide_an_invalid_api_key(monkeypatch):
    original_client = httpx.Client
    requests = []

    def dispatch(incoming):
        requests.append(incoming)
        if "Authorization" in incoming.headers:
            return httpx.Response(401, text="private error containing " + KEY)
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(dispatch)
    monkeypatch.setattr("packages.providers.http.httpx.Client",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    provider = JsonProvider(minimum_interval=0)
    assert provider.request("GET", "https://api.openalex.org/works", cache=True) == {"results": []}
    assert provider.requests_used == 1
    assert provider.request("GET", "https://api.openalex.org/works", cache=True) == {"results": []}
    assert provider.requests_used == 0
    with pytest.raises(ProviderError) as caught:
        provider.request("GET", "https://api.openalex.org/works", cache=True,
                         headers={"Authorization": "Bearer " + KEY})
    assert len(requests) == 2
    assert provider.requests_used == 1
    assert KEY not in str(caught.value)
    assert KEY not in json.dumps(list(provider._cache))

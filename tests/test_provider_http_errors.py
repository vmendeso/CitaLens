"""HTTP diagnostics must explain permission failures without echoing secrets."""
import traceback

import httpx
import pytest

from packages.providers.http import JsonProvider, ProviderError


@pytest.fixture
def failed_request(monkeypatch):
    original = httpx.Client
    requests = []
    monkeypatch.setattr("packages.providers.http.time.sleep", lambda _: None)

    def run(status, body, host="api.groq.com", *, as_json=True):
        def handle(request):
            requests.append(request)
            return httpx.Response(status, **({"json": body} if as_json else {"text": body}))
        monkeypatch.setattr("packages.providers.http.httpx.Client",
                            lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
        provider = JsonProvider(minimum_interval=0)
        parameters = {"method": "POST", "url": f"https://{host}/completion?api_key=SECRET_QUERY",
                      "headers": {"Authorization": "Bearer SECRET_HEADER"},
                      "payload": {"messages": ["PRIVATE_PROMPT"]}, "max_attempts": 1}
        with pytest.raises(ProviderError) as caught:
            provider.request(**parameters)
        assert provider.requests_used == 1
        return caught.value
    return run


@pytest.mark.parametrize("code", ["model_permission_blocked_org", "model_permission_blocked_project"])
def test_known_groq_permission_error_preserves_only_safe_diagnostics(failed_request, code):
    error = failed_request(403, {"error": {"code": code, "message": "SECRET_BODY PRIVATE_PROMPT",
                                          "api_key": "SECRET_HEADER"}})
    assert error.status_code == 403
    assert error.provider_code == code
    assert str(error) == "Falha do provedor api.groq.com: HTTP_403."
    visible = str(error) + repr(vars(error)) + "".join(traceback.format_exception(error))
    for secret in ("SECRET_BODY", "SECRET_QUERY", "SECRET_HEADER", "PRIVATE_PROMPT"):
        assert secret not in visible


@pytest.mark.parametrize("body", [
    {"error": {"code": "SECRET_CUSTOM_CODE", "message": "SECRET_BODY"}},
    {"error": {"code": ["model_permission_blocked_org"]}},
    {"error": "SECRET_BODY"}, ["SECRET_BODY"], None,
])
def test_unknown_or_malformed_provider_payload_stays_generic(failed_request, body):
    error = failed_request(403, body)
    assert error.status_code == 403
    assert error.provider_code is None
    assert "SECRET" not in str(error) + repr(vars(error))


@pytest.mark.parametrize("host,status", [
    ("api.groq.com.evil.example", 403), ("api.openalex.org", 403), ("api.groq.com", 400),
    ("api.groq.com", 401), ("api.groq.com", 429), ("api.groq.com", 500),
])
def test_permission_code_is_scoped_to_groq_http_403(failed_request, host, status):
    error = failed_request(status, {"error": {"code": "model_permission_blocked_org"}}, host)
    assert error.status_code == status
    assert error.provider_code is None


@pytest.mark.parametrize("body", ["<html>SECRET_BODY</html>", "x" * 70_000], ids=["html", "oversized"])
def test_non_json_and_oversized_error_bodies_do_not_break_handling(failed_request, body):
    error = failed_request(403, body, as_json=False)
    assert error.status_code == 403
    assert error.provider_code is None
    assert "SECRET_BODY" not in str(error)


def test_other_provider_errors_keep_optional_diagnostics_empty():
    error = ProviderError("A safe local validation message.")
    assert error.status_code is error.provider_code is None
    unsafe = ProviderError("Safe.", status_code=True, provider_code="SECRET_CUSTOM_CODE")
    assert unsafe.status_code is unsafe.provider_code is None

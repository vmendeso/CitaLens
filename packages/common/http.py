import httpx
from packages.common.config import get_settings


def client(timeout=None):
    settings = get_settings()
    return httpx.Client(
        headers={"X-Internal-Token": settings.internal_token},
        timeout=timeout or settings.request_timeout, trust_env=False,
    )


def call(base, path, payload=None, method="POST", timeout=None):
    with client(timeout) as http:
        response = http.request(method, base.rstrip("/") + path, json=payload)
        response.raise_for_status()
        return response.json()

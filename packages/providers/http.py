"""Bounded external requests and a DNS-pinned public-document downloader."""
from __future__ import annotations

import copy
import concurrent.futures
import http.client
import ipaddress
import json
import hashlib
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

_DNS_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="source-dns")
_DNS_SLOTS = threading.BoundedSemaphore(16)
# https://console.groq.com/docs/model-permissions
_SAFE_PROVIDER_CODES = frozenset({"model_permission_blocked_org", "model_permission_blocked_project"})


class ProviderError(RuntimeError):
    """Safe public message plus optional, bounded machine-readable diagnostics."""
    def __init__(self, message: str = "", *, status_code: int | None = None,
                 provider_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
        self.provider_code = (provider_code if isinstance(provider_code, str)
                              and provider_code in _SAFE_PROVIDER_CODES else None)


def _safe_provider_code(host: str, response: httpx.Response) -> str | None:
    # Only documented Groq permission codes are exposed. Never keep arbitrary
    # provider messages, request URLs, echoed prompts or credentials in an error.
    if host != "api.groq.com" or response.status_code != 403 or len(response.content) > 65_536:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in _SAFE_PROVIDER_CODES else None


class UnsafeURL(ProviderError):
    pass


def public_target(url: str, timeout: float = 5) -> tuple[str, str, int, str, str]:
    """Validate *all* DNS answers and return one pinned IP, never re-resolve it."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeURL("Somente URLs HTTP(S) públicas são permitidas.")
    if parts.username or parts.password or "\\" in url or any(ord(c) < 32 for c in url):
        raise UnsafeURL("URL com credenciais ou caracteres inválidos.")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("Porta inválida.") from exc
    if port not in {80, 443}:
        raise UnsafeURL("Porta não permitida para documentos externos.")
    if host.lower() in {"localhost", "metadata.google.internal"} or host.lower().endswith((".local", ".internal", ".localhost")):
        raise UnsafeURL("Host interno bloqueado.")
    if not _DNS_SLOTS.acquire(blocking=False):
        raise ProviderError("Fila de resolução DNS ocupada; tente novamente.")
    try:
        resolution = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        resolution.add_done_callback(lambda _: _DNS_SLOTS.release())
        addresses = resolution.result(timeout=max(.1, timeout))
    except (OSError, concurrent.futures.TimeoutError) as exc:
        raise ProviderError("Falha na resolução DNS.") from exc
    ips = list(dict.fromkeys(item[4][0] for item in addresses))
    if not ips:
        raise UnsafeURL("Nenhum endereço público.")
    for address in ips:
        ip = ipaddress.ip_address(address)
        mapped = getattr(ip, "ipv4_mapped", None)
        # Transition ranges can route to embedded addresses; fail closed.
        if (not ip.is_global or (mapped and not mapped.is_global)
                or ip in ipaddress.ip_network("2002::/16") or ip in ipaddress.ip_network("64:ff9b::/96")):
            raise UnsafeURL("Endereço privado, reservado ou de metadados bloqueado.")
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return parts.scheme, host, port, ips[0], path


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip: str, port: int, timeout: float):
        super().__init__(hostname, port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


@dataclass(frozen=True)
class Download:
    data: bytes
    final_url: str
    content_type: str
    status: int


def download_public(url: str, *, max_bytes: int = 20_000_000, timeout: float = 25,
                    max_redirects: int = 4, on_request=None, openalex_api_key: str = "") -> Download:
    """DNS-pinned download; optional OpenAlex auth is restricted to its first hop."""
    if openalex_api_key:
        # Never send this credential to publisher URLs or redirect destinations.
        if not re.fullmatch(r"https://content\.openalex\.org/works/W[0-9]+\.pdf", url):
            raise UnsafeURL("Credencial OpenAlex permitida somente no endpoint oficial de PDF.")
    deadline = time.monotonic() + timeout
    current = url
    for hop in range(max_redirects + 1):
        scheme, host, port, address, path = public_target(current, min(5, max(.1, deadline - time.monotonic())))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("Tempo máximo de download excedido.")
        if scheme == "https":
            conn = _PinnedHTTPS(host, address, port, remaining)
        else:
            conn = http.client.HTTPConnection(address, port, timeout=remaining)
        deadline_timer = None
        try:
            # HTTPConnection timeouts measure inactivity. An absolute timer also
            # interrupts slow headers/body peers that keep sending occasional bytes.
            conn.connect()
            connection_socket = conn.sock
            def abort_at_deadline():
                try:
                    connection_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                finally:
                    connection_socket.close()
            deadline_timer = threading.Timer(max(.001, deadline - time.monotonic()), abort_at_deadline)
            deadline_timer.daemon = True
            deadline_timer.start()
            if on_request:
                on_request()
            headers = {
                "Host": host, "User-Agent": "CitaLens/0.1 bibliographic-review",
                "Accept": "application/pdf,text/html,text/plain",
                "Accept-Encoding": "identity",
            }
            if openalex_api_key and hop == 0:
                headers["Authorization"] = "Bearer " + openalex_api_key
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or hop == max_redirects:
                    raise ProviderError("Redirecionamentos excedidos ou sem destino.")
                current = urljoin(current, location)
                continue
            if response.status in {401, 403, 451}:
                raise ProviderError("ACCESS_RESTRICTED")
            if response.status != 200:
                raise ProviderError(f"HTTP_{response.status}")
            kind = response.getheader("Content-Type", "").split(";")[0].lower().strip()
            if kind not in {"application/pdf", "text/html", "application/xhtml+xml", "text/plain"}:
                raise ProviderError("Tipo de conteúdo externo não permitido.")
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise ProviderError("Conteúdo comprimido não solicitado.")
            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise ProviderError("Fonte excede o limite de tamanho.")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError("Tempo máximo de download excedido.")
                if conn.sock:
                    conn.sock.settimeout(remaining)
                # read1 returns after one socket read; read(n) could be held indefinitely
                # by a peer trickling bytes without ever reaching n or an inactivity timeout.
                part = response.read1(min(65536, max_bytes + 1 - size))
                if not part:
                    break
                size += len(part)
                if size > max_bytes:
                    raise ProviderError("Fonte excede o limite de tamanho.")
                chunks.append(part)
            return Download(b"".join(chunks), current, kind, response.status)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(f"Falha de download: {type(exc).__name__}.") from exc
        finally:
            if deadline_timer:
                deadline_timer.cancel()
            conn.close()
    raise ProviderError("Limite de redirecionamentos.")


class JsonProvider:
    """Process-local bounded TTL cache, serial rate limit and two finite retries."""
    def __init__(self, timeout: float = 25, minimum_interval: float = 0.5, ttl: float = 900):
        self.timeout, self.minimum_interval, self.ttl = timeout, minimum_interval, ttl
        self._cache: dict[str, tuple[float, dict]] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stats = threading.local()

    @property
    def requests_used(self):
        return getattr(self._stats, "requests_used", 0)

    def reset_usage(self):
        self._stats.requests_used = 0

    def request(self, method: str, url: str, *, params=None, payload=None, headers=None, cache=False, max_attempts=3):
        self.reset_usage()
        # A newly configured/revoked key must not reuse another credential's cache.
        credentials = json.dumps(headers or {}, sort_keys=True, default=str)
        credential_hash = hashlib.sha256(credentials.encode()).hexdigest()
        key = json.dumps([method, url, params, payload, credential_hash], sort_keys=True, default=str)
        now = time.monotonic()
        with self._lock:
            if cache and key in self._cache and now - self._cache[key][0] < self.ttl:
                return copy.deepcopy(self._cache[key][1])
        host = urlsplit(url).hostname
        for attempt in range(max(0, min(3, max_attempts))):
            with self._lock:
                wait = self.minimum_interval - (time.monotonic() - self._last.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                self._last[host] = time.monotonic()
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
                    self._stats.requests_used += 1
                    response = client.request(method, url, params=params, json=payload, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < min(3, max_attempts) - 1:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                response.raise_for_status()
                if len(response.content) > 8_000_000:
                    raise ProviderError("Resposta do provedor excede o limite.")
                data = response.json()
                if cache:
                    with self._lock:
                        if len(self._cache) >= 128:
                            self._cache.pop(next(iter(self._cache)))
                        self._cache[key] = (time.monotonic(), copy.deepcopy(data))
                return data
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < min(3, max_attempts) - 1 and isinstance(exc, httpx.TransportError):
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                if isinstance(exc, httpx.HTTPStatusError):
                    raise ProviderError(f"Falha do provedor {host}: HTTP_{exc.response.status_code}.",
                                        status_code=exc.response.status_code,
                                        provider_code=_safe_provider_code(host, exc.response)) from None
                raise ProviderError(f"Falha do provedor {host}: {type(exc).__name__}.") from exc
        raise ProviderError("Tentativas esgotadas.")

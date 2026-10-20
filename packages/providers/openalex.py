"""OpenAlex bibliographic discovery, without treating metadata as evidence."""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from packages.providers.http import JsonProvider, ProviderError


API_BASE = "https://api.openalex.org"
_INVALID_RESPONSE = "Resposta OpenAlex inválida; metadados não puderam ser validados."


def _doi(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError(_INVALID_RESPONSE)
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).rstrip(" .").lower()


def _text(value, *, required=False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ProviderError(_INVALID_RESPONSE)
    return value.strip()


def _title_query(title: str) -> str:
    """Reference titles are text, never OpenAlex query-language expressions.

    The API treats punctuation such as '?' as a wildcard, even in a title,
    and rejects wildcards in stemmed searches. Normalize only the outbound
    query; identity matching must still compare the original metadata.
    """
    words = re.sub(r"[^\w\s]", " ", title[:600], flags=re.UNICODE).split()
    return " ".join(word.lower() if word in {"AND", "OR", "NOT"} else word for word in words)


def _object(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderError(_INVALID_RESPONSE)
    return value


def _public_url(value) -> str | None:
    """Only URL syntax here; the downloader still pins and validates public DNS."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError(_INVALID_RESPONSE)
    if not value or "\\" in value or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in {None, 80, 443}):
            return None
    except ValueError:
        return None
    return value


def openalex_work(item: dict) -> dict:
    """Normalize one work, preserving the external ID separately from SQL IDs."""
    if not isinstance(item, dict):
        raise ProviderError(_INVALID_RESPONSE)
    external_id = _text(item.get("id"), required=True)
    matched = re.fullmatch(r"(?:https://openalex\.org/)?(W[1-9][0-9]*)", external_id)
    if not matched:
        raise ProviderError(_INVALID_RESPONSE)
    work_id = matched.group(1)
    title = _text(item.get("title") or item.get("display_name"), required=True)
    year = item.get("publication_year")
    if year is not None and (type(year) is not int or not 1 <= year <= 9999):
        raise ProviderError(_INVALID_RESPONSE)
    authorships = item.get("authorships", [])
    if not isinstance(authorships, list):
        raise ProviderError(_INVALID_RESPONSE)
    authors = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            raise ProviderError(_INVALID_RESPONSE)
        author = _object(authorship.get("author"))
        name = _text(author.get("display_name"))
        if name:
            authors.append({"name": name})

    best = _object(item.get("best_oa_location"))
    primary = _object(item.get("primary_location"))
    locations = item.get("locations", [])
    if not isinstance(locations, list):
        raise ProviderError(_INVALID_RESPONSE)
    public_pdf_urls = []
    # Prefer the best open location, then retain the provider's location order.
    for location in [best, *locations]:
        if not isinstance(location, dict):
            raise ProviderError(_INVALID_RESPONSE)
        if location.get("is_oa") is not True:
            continue
        pdf = _public_url(location.get("pdf_url"))
        if (pdf and urlsplit(pdf).hostname not in {"api.openalex.org", "content.openalex.org"}
                and pdf not in public_pdf_urls):
            public_pdf_urls.append(pdf)

    content = _object(item.get("content_urls"))
    advertised_pdf = content.get("pdf")
    if advertised_pdf is not None and not isinstance(advertised_pdf, str):
        raise ProviderError(_INVALID_RESPONSE)
    content_pdf_url = f"https://content.openalex.org/works/{work_id}.pdf"
    if advertised_pdf != content_pdf_url:
        content_pdf_url = None
    doi = _doi(item.get("doi"))
    landing = _public_url(best.get("landing_page_url")) or _public_url(primary.get("landing_page_url"))
    source = _object(best.get("source") or primary.get("source"))
    return {
        "doi": doi, "title": title, "authors": authors, "year": year,
        "type": _text(item.get("type")) or None,
        "publisher": _text(source.get("host_organization_name")) or None,
        "url": landing or ("https://doi.org/" + quote(doi, safe="/") if doi else None),
        "metadata_origin": "OpenAlex",
        "metadata_url": f"{API_BASE}/works/{work_id}",
        "openalex_id": f"https://openalex.org/{work_id}",
        "public_pdf_urls": public_pdf_urls, "content_pdf_url": content_pdf_url,
    }


class OpenAlexProvider:
    def __init__(self, settings, http: JsonProvider):
        self.settings = settings
        # Share cache, bounded retries and request accounting with the resolver.
        self.http = http

    def lookup(self, reference: dict, max_attempts: int = 3) -> list[dict]:
        self.http.reset_usage()
        doi = _doi(reference.get("doi"))
        title = _text(reference.get("title"))
        if not doi and not title:
            raise ProviderError("OpenAlex requer DOI ou título da referência.")
        if max_attempts <= 0:
            raise ProviderError("Limite de consultas externas esgotado.")
        params = {}
        if doi:
            endpoint = API_BASE + "/works/https://doi.org/" + quote(doi, safe="/")
        else:
            endpoint = API_BASE + "/works"
            query = _title_query(title)
            if not query:
                raise ProviderError("OpenAlex requer um título com palavras pesquisáveis.")
            params = {"search.title": query, "per_page": 5}
        headers = {"Accept": "application/json", "User-Agent": "CitaLens/0.1 bibliographic-review"}
        key = self.settings.openalex_api_key.strip()
        if key:
            headers["Authorization"] = "Bearer " + key
        result = self.http.request("GET", endpoint, params=params, headers=headers,
                                   cache=True, max_attempts=min(3, max_attempts))
        if not isinstance(result, dict):
            raise ProviderError(_INVALID_RESPONSE)
        items = [result] if doi else result.get("results")
        if not isinstance(items, list):
            raise ProviderError(_INVALID_RESPONSE)
        return [openalex_work(item) for item in items[:5]]

"""Bibliographic identity, real discovery, controlled source acquisition and demo."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx

from packages.providers.http import JsonProvider, ProviderError, download_public
from packages.providers.openalex import OpenAlexProvider

DEMO_TITLE = "Efeito de pausas na atenção: estudo sintético"
DEMO_DOI = "10.0000/citalens.demo"
DEMO_TEXT = (
    "Estudo sintético com 40 adultos observou aumento de 12% na atenção após pausas de cinco minutos. "
    "O desenho observacional não demonstra causalidade. "
    "Os resultados não devem ser generalizados para crianças ou para toda a população."
)


def normalized(value) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()))


def normalize_doi(value) -> str:
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I).rstrip(" .").lower()


def author_names(reference: dict) -> list[str]:
    authors = reference.get("authors", []) or []
    if isinstance(authors, str):
        authors = re.split(r";| and | e ", authors)
    return [
        normalized(a.get("family") or a.get("name") or a.get("surname") or "") if isinstance(a, dict)
        # Normalization removes punctuation, so surname-first notation must be
        # split before it turns "Shoji, M" into the unmatched "shoji m".
        else normalized(str(a).split(",", 1)[0]) for a in authors
    ]


def crossref_work(item: dict) -> dict:
    date = item.get("published", item.get("issued", {})).get("date-parts", [[]])
    return {
        "doi": normalize_doi(item.get("DOI")),
        "title": (item.get("title") or [""])[0],
        "authors": item.get("author", []),
        "year": date[0][0] if date and date[0] else None,
        "publisher": item.get("publisher"), "type": item.get("type"),
        "edition": item.get("edition-number"), "url": item.get("URL"),
        "metadata_origin": "Crossref REST API", "links": item.get("link", []),
        "abstract": item.get("abstract"),
    }


def identity_match(reference: dict, work: dict) -> tuple[str, list[str]]:
    """A resolved DOI is never sufficient when the supplied metadata conflict."""
    limitations, conflicts = [], []
    reference_doi, candidate_doi = normalize_doi(reference.get("doi")), normalize_doi(work.get("doi"))
    if reference_doi and candidate_doi and reference_doi != candidate_doi:
        conflicts.append("DOI encontrado difere do DOI informado.")
    title, candidate = normalized(reference.get("title", "")), normalized(work.get("title", ""))
    if not title or not candidate:
        limitations.append("Título insuficiente para validar a identidade da obra.")
    elif SequenceMatcher(None, title, candidate).ratio() < 0.86:
        conflicts.append("Título do registro externo diverge da referência.")
    year = re.search(r"\d{4}", str(reference.get("year", "")))
    other_year = re.search(r"\d{4}", str(work.get("year", "")))
    if year and other_year and year.group() != other_year.group():
        conflicts.append("Ano diverge; publicação antecipada ou edição requer revisão humana.")
    expected, found = author_names(reference), author_names(work)
    if expected and found and not any(
        a and b and (a in b or b in a or a.split()[-1] == b.split()[-1])
        for a in expected for b in found
    ):
        conflicts.append("Autoria não corresponde ao registro externo.")
    if reference.get("edition") and work.get("edition") and normalized(reference["edition"]) != normalized(work["edition"]):
        conflicts.append("Edição divergente.")
    if conflicts:
        return "METADATA_MISMATCH", conflicts
    if not title or not candidate or not ((expected and found) or (reference_doi and candidate_doi)):
        return "UNRESOLVED", limitations + ["Identidade requer título e autoria ou DOI corroborado."]
    return "MATCHED", limitations


class _HTMLText(HTMLParser):
    excluded = {"script", "style", "nav", "noscript", "table", "figure", "figcaption",
                "svg", "canvas", "math", "pre", "code", "ul", "ol", "header", "footer",
                "aside", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.parts, self.meta = [], {}
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.split(":")[-1]
        attrs = dict(attrs)
        if tag in self.excluded:
            self.skip += 1
        if tag == "meta":
            key = attrs.get("name", attrs.get("property", "")).lower()
            self.meta.setdefault(key, []).append(attrs.get("content", ""))
        if not self.skip:
            if tag in {"p", "div", "section"}:
                self.parts.append("\n\n")
            elif tag == "br":
                self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.split(":")[-1]
        if tag in self.excluded and self.skip:
            self.skip -= 1
        if not self.skip and tag in {"p", "div", "section"}:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            # HTML source indentation is not a paragraph boundary.
            self.parts.append(re.sub(r"\s+", " ", data))


def plain_html(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    return re.sub(r"\n[ \t]*\n+", "\n\n", text).strip()


def page(text: str, number=1, method="SOURCE_TEXT", label=None) -> dict:
    return {
        "physical_index": number - 1, "physical_page_number": number,
        "printed_label": label, "raw_text": text, "normalized_text": text,
        "extraction_method": method, "blocks": [{
            "text": text, "bbox": None, "order": 0, "offset_start": 0,
            "offset_end": len(text), "coordinate_space": "text", "approximate": True,
        }], "quality": {"characters": len(text), "offset_basis": "normalized_text"},
        "status": "EXTRACTED" if text.strip() else "UNVERIFIABLE",
        "error": None if text.strip() else "Página da fonte sem texto extraível; OCR necessário.",
    }


def source_record(request, work, pages, *, origin_url, final_url, digest, access_status, version="1", **extra):
    work_id = str(request.reference.get("work_id") or work.get("id") or uuid5(
        NAMESPACE_URL, f"{request.user_id}/{request.document_id}/{work.get('doi') or normalized(work.get('title'))}"
    ))
    work["id"] = work_id
    return {
        "id": str(uuid5(NAMESPACE_URL, f"{request.user_id}/{request.document_id}/{work_id}/{version}/{digest}")),
        "user_id": request.user_id, "document_id": request.document_id,
        "work_id": work_id, "version": version, "origin": "SOURCE",
        "origin_url": origin_url, "final_url": final_url,
        "accessed_at": datetime.now(timezone.utc).isoformat(), "content_hash": digest,
        "pages": pages, "access_status": access_status, **extra,
    }


def demo_resolve(request):
    reference = request.reference
    if normalize_doi(reference.get("doi")) != DEMO_DOI and normalized(reference.get("title")) != normalized(DEMO_TITLE):
        return {"identity_status": "UNRESOLVED", "access_status": "NOT_FOUND", "work": {},
                "source": None, "attempts": [], "external_queries": 0,
                "limitations": ["DEMONSTRAÇÃO: referência não faz parte das fixtures anotadas."]}
    work = {"title": DEMO_TITLE, "doi": DEMO_DOI, "authors": [{"family": "Silva"}],
            "year": 2024, "type": "synthetic-fixture", "metadata_origin": "DEMO_FIXTURE"}
    status, limitations = identity_match(reference, work)
    source = None
    if status == "MATCHED":
        source = source_record(request, work, [page(DEMO_TEXT, method="DEMO_FIXTURE")],
                               origin_url="fixture://citalens/source-v1", final_url="fixture://citalens/source-v1",
                               digest=hashlib.sha256(DEMO_TEXT.encode()).hexdigest(), access_status="FULL_TEXT",
                               is_demo=True, identity_status=status)
    return {"identity_status": status, "access_status": "FULL_TEXT" if source else "METADATA_ONLY", "external_queries": 0,
            "work": work, "source": source, "attempts": [{
                "provider": "DEMO_FIXTURE", "query": DEMO_TITLE, "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": "synthetic_annotation", "identity_status": status,
            }], "limitations": ["DEMONSTRAÇÃO: fonte sintética local; nenhuma pesquisa web foi realizada."] + limitations}


class SourceResolver:
    def __init__(self, settings):
        self.settings = settings
        self.http = JsonProvider(timeout=settings.request_timeout)

    def resolve(self, request):
        if request.mode == "demo":
            return demo_resolve(request)
        ref = request.reference
        attempts, limitations, candidates = [], [], []
        budget = max(0, min(self.settings.max_external_queries,
                           request.max_external_queries if request.max_external_queries is not None else self.settings.max_external_queries))
        initial_budget = budget
        if budget == 0:
            return {"identity_status": "UNRESOLVED", "access_status": "NOT_FOUND", "work": {}, "source": None,
                    "attempts": [], "external_queries": 0, "limitations": ["Limite de consultas externas esgotado."]}
        doi = normalize_doi(ref.get("doi"))
        query = " ".join(filter(None, [
            str(ref.get("title") or ""), " ".join(author_names(ref))[:200], str(ref.get("year") or "")
        ]))[:600]
        try:
            if budget <= 0:
                raise ProviderError("Limite de consultas externas esgotado.")
            params = {"mailto": self.settings.crossref_mailto} if self.settings.crossref_mailto else {}
            if doi:
                endpoint = "https://api.crossref.org/works/" + quote(doi, safe="")
            else:
                endpoint = "https://api.crossref.org/works"
                params.update({"query.bibliographic": query, "rows": 3})
            result = self.http.request("GET", endpoint, params=params, cache=True,
                                       max_attempts=min(3, budget),
                                       headers={"User-Agent": "CitaLens/0.1 bibliographic-review"})
            budget -= self.http.requests_used
            items = [result["message"]] if doi else result["message"].get("items", [])
            candidates = [crossref_work(item) for item in items]
            attempts.append(self._attempt("crossref", doi or query, endpoint, "metadata_received", metadata=candidates))
        except (ProviderError, KeyError, TypeError) as exc:
            budget = min(budget, initial_budget - self.http.requests_used)
            limitations.append(f"Consulta Crossref indisponível: {exc}")
            attempts.append(self._attempt("crossref", doi or query, None, "error", error=str(exc)))
        chosen, status = {}, "UNRESOLVED"
        for candidate in candidates:
            matched, reasons = identity_match(ref, candidate)
            if matched == "MATCHED" or (doi and matched == "METADATA_MISMATCH"):
                chosen, status = candidate, matched
                limitations.extend(reasons)
                break
        # Academic metadata can recover a work absent from Crossref, or add
        # verified open-access locations to an already identified work.
        if self.settings.openalex_enabled and status != "METADATA_MISMATCH" and (doi or ref.get("title")):
            if budget > 0:
                lookup = dict(ref)
                if status == "MATCHED" and chosen.get("doi"):
                    lookup["doi"] = chosen["doi"]
                try:
                    found = OpenAlexProvider(self.settings, self.http).lookup(lookup, max_attempts=min(3, budget))
                    candidates.extend(found)
                    attempts.append(self._attempt("openalex", lookup.get("doi") or ref.get("title"),
                                                  None, "metadata_received", metadata=found))
                    for candidate in found:
                        matched, reasons = identity_match(ref, candidate)
                        if lookup.get("doi") and normalize_doi(candidate.get("doi")) != normalize_doi(lookup["doi"]):
                            matched, reasons = "METADATA_MISMATCH", ["DOI do OpenAlex não corresponde à obra consultada."]
                        if matched == "MATCHED" and status == "MATCHED":
                            matched, reasons = identity_match(chosen, candidate)
                        if matched != "MATCHED":
                            if lookup.get("doi"):
                                limitations.extend(["OpenAlex: " + reason for reason in reasons])
                                if status == "UNRESOLVED":
                                    chosen, status = candidate, matched
                            continue
                        if status == "MATCHED":
                            # Retain Crossref's metadata and abstract provenance.
                            chosen = dict(chosen) | {key: candidate[key] for key in (
                                "openalex_id", "public_pdf_urls", "content_pdf_url")}
                        else:
                            chosen, status = candidate, "MATCHED"
                        break
                except ProviderError as exc:
                    limitations.append(f"Consulta OpenAlex indisponível: {exc}")
                    attempts.append(self._attempt("openalex", lookup.get("doi") or ref.get("title"),
                                                  None, "error", error=str(exc)))
                finally:
                    budget = max(0, budget - self.http.requests_used)
            else:
                limitations.append("Limite de consultas externas impede consulta ao OpenAlex.")

        # General web results only discover download URLs; they cannot establish
        # identity. Do not spend queries on URLs that would be discarded below.
        if status != "MATCHED":
            if status == "UNRESOLVED":
                limitations.append("Obra não confirmada; ausência em índices não prova inexistência.")
            limitations.append("Busca web não executada: a identidade bibliográfica da obra precisa ser confirmada.")
            return {"identity_status": status, "access_status": "METADATA_ONLY" if candidates else "NOT_FOUND",
                    "work": chosen, "source": None, "attempts": attempts, "limitations": limitations,
                    "external_queries": initial_budget - budget}

        public_pdfs = chosen.get("public_pdf_urls", [])
        content_pdf = chosen.get("content_pdf_url")
        if content_pdf and not self.settings.openalex_api_key.strip():
            limitations.append("PDF do acervo OpenAlex exige OPENALEX_API_KEY; links públicos continuam disponíveis.")
            content_pdf = None
        academic_urls = list(dict.fromkeys(public_pdfs[:3] + ([content_pdf] if content_pdf else [])))
        searched_web = not academic_urls
        urls, budget = self._discover_web(query or doi, budget, attempts, limitations) if searched_web else ([], budget)
        source = None
        access = "METADATA_ONLY"
        preferred = [link.get("URL") for link in chosen.get("links", []) if link.get("URL")]
        if ref.get("url"):
            preferred.append(ref["url"])
        if chosen.get("url"):
            preferred.append(chosen["url"])
        # At most five distinct downloads, including a later general-web fallback.
        pending = list(dict.fromkeys(academic_urls + preferred + urls))
        visited = set()
        while budget > 0 and len(visited) < 5:
            if not pending:
                if searched_web:
                    break
                urls, budget = self._discover_web(query or doi, budget, attempts, limitations)
                searched_web = True
                pending = [url for url in urls if url not in visited]
                if not pending or budget <= 0:
                    break
            url = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            download_provider = "openalex_content" if url == content_pdf else "public_download"
            try:
                def charge_request():
                    nonlocal budget
                    if budget <= 0:
                        raise ProviderError("Limite de consultas externas esgotado.")
                    budget -= 1
                auth = {"openalex_api_key": self.settings.openalex_api_key.strip()} if url == content_pdf else {}
                downloaded = download_public(url, max_bytes=self.settings.max_source_bytes,
                                             timeout=self.settings.request_timeout,
                                             max_redirects=min(4, max(0, budget - 1)), on_request=charge_request, **auth)
                pages, access, extraction_limitations = self._extract(downloaded, chosen)
                limitations.extend(extraction_limitations)
                attempts.append(self._attempt(download_provider, query, url, "content_checked",
                                              final_url=downloaded.final_url, access_status=access))
                if pages:
                    source = source_record(
                        request, chosen, pages, origin_url=url, final_url=downloaded.final_url,
                        digest=hashlib.sha256(downloaded.data).hexdigest(), access_status=access,
                        identity_status="MATCHED", is_demo=False,
                    )
                    self._preserve(request, source, downloaded.data, downloaded.content_type, limitations)
                    break
            except (ProviderError, ValueError) as exc:
                access = "RESTRICTED" if "ACCESS_RESTRICTED" in str(exc) else "FETCH_ERROR"
                attempts.append(self._attempt(download_provider, query, url, "failed", error=str(exc)))
        if not source and chosen.get("abstract"):
            abstract = plain_html(chosen["abstract"])
            if abstract:
                source = source_record(
                    request, chosen, [page(abstract, method="CROSSREF_ABSTRACT")],
                    origin_url="https://api.crossref.org/works/" + quote(chosen["doi"], safe=""),
                    final_url="https://api.crossref.org/works/" + quote(chosen["doi"], safe=""),
                    digest=hashlib.sha256(abstract.encode()).hexdigest(), access_status="ABSTRACT_ONLY",
                    identity_status="MATCHED", is_demo=False,
                )
                access = "ABSTRACT_ONLY"
                self._preserve(request, source, abstract.encode("utf-8"), "text/plain", limitations)
                limitations.append("Somente resumo fornecido pelo depositante no Crossref; não substitui texto integral.")
        if not source:
            limitations.append("Conteúdo da obra não obtido com identidade verificável; anexe uma cópia autorizada.")
        return {"identity_status": status, "access_status": access, "work": chosen, "source": source,
                "attempts": attempts, "limitations": limitations, "external_queries": initial_budget - budget}

    def _discover_web(self, query, budget, attempts, limitations):
        urls = []
        web_provider = self.settings.web_provider
        if web_provider == "none":
            limitations.append("Busca web desativada; somente fontes e metadados já identificados serão consultados.")
        elif web_provider not in {"brave", "searxng"}:
            limitations.append(f"Provedor de busca web não suportado: {web_provider}.")
        elif web_provider == "brave" and not self.settings.brave_api_key:
            limitations.append("Busca web Brave não configurada; credencial BRAVE_API_KEY ausente.")
        elif budget > 0:
            requested = False
            try:
                if web_provider == "searxng":
                    endpoint = self.settings.searxng_base_url.rstrip("/") + "/search"
                    params = {"q": query, "format": "json", "categories": "general", "safesearch": 1}
                    headers = {"Accept": "application/json"}
                else:
                    endpoint = "https://api.search.brave.com/res/v1/web/search"
                    params = {"q": query, "count": 5}
                    headers = {"X-Subscription-Token": self.settings.brave_api_key, "Accept": "application/json"}
                requested = True
                result = self.http.request("GET", endpoint, params=params, headers=headers,
                                           cache=True, max_attempts=min(3, budget))
                if not isinstance(result, dict):
                    raise ProviderError("Resposta de busca web inválida; esperado um objeto JSON.")
                found = result.get("results") if web_provider == "searxng" else result.get("web", {}).get("results", [])
                if not isinstance(found, list):
                    raise ProviderError("Resposta de busca web inválida; lista de resultados ausente.")
                urls = list(dict.fromkeys(item["url"] for item in found
                            if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].strip()))[:5]
                if web_provider == "searxng" and result.get("unresponsive_engines"):
                    limitations.append("SearXNG informou mecanismos indisponíveis; a descoberta pode estar incompleta.")
                attempts.append(self._attempt(web_provider, query, None, "discovery_only",
                                              urls=urls, note="Snippets descartados; não são evidência."))
            except (ProviderError, KeyError, TypeError, AttributeError) as exc:
                message = str(exc) if isinstance(exc, ProviderError) else "Resposta de busca web inválida."
                limitations.append(message)
                attempts.append(self._attempt(web_provider, query, None, "error", error=message))
            finally:
                if requested:
                    budget = max(0, budget - self.http.requests_used)
        else:
            limitations.append("Limite de consultas externas impede busca web adicional.")
        return urls, budget

    def _extract(self, downloaded, work):
        """Conservative content gate: metadata match is not sufficient to trust a URL."""
        if downloaded.content_type == "application/pdf":
            if not downloaded.data.startswith(b"%PDF-"):
                raise ProviderError("Assinatura PDF inválida.")
            import fitz
            try:
                pdf = fitz.open(stream=downloaded.data, filetype="pdf")
            except Exception as exc:
                raise ProviderError("PDF da fonte corrompido.") from exc
            with pdf:
                if pdf.needs_pass:
                    raise ProviderError("ACCESS_RESTRICTED: PDF protegido.")
                if len(pdf) > self.settings.max_source_pages:
                    raise ProviderError("PDF da fonte excede limite de páginas.")
                first = normalized("\n".join(pdf[i].get_text() for i in range(min(2, len(pdf)))))
                title_tokens = normalized(work["title"]).split()
                title_present = (normalized(work["title"]) in first or
                    (len(title_tokens) >= 5 and sum(t in first.split() for t in title_tokens) / len(title_tokens) >= .95))
                authors = author_names(work)
                if not title_present or not any(a.split()[-1] in first.split() for a in authors if a):
                    raise ProviderError("Identidade do PDF não corroborada por título e autoria nas primeiras páginas.")
                from packages.document_processing.pdf import extract_pdf
                extracted = extract_pdf(
                    downloaded.data, ocr_enabled=False, max_pages=self.settings.max_source_pages,
                    max_seconds=max(1, self.settings.stage_timeout - 10),
                )
                pages = extracted["pages"]
                partial = any(not p["normalized_text"].strip() for p in pages)
                # Completeness/edition cannot be established merely from a successful PDF download.
                return pages, "PARTIAL_TEXT", extracted.get("warnings", []) + [
                    "PDF consultado; correspondência automática por título/autoria requer revisão de edição.",
                    "Cobertura integral da obra não foi comprovada." +
                    (" Há páginas sem texto extraível." if partial else ""),
                ]
        parser = _HTMLText()
        parser.feed(downloaded.data.decode("utf-8", errors="replace"))
        meta = parser.meta
        candidate = {
            "title": (meta.get("citation_title") or [""])[0],
            "doi": (meta.get("citation_doi") or [""])[0],
            "authors": meta.get("citation_author", []),
            "year": (meta.get("citation_publication_date") or meta.get("citation_date") or [""])[0],
        }
        status, _ = identity_match(work, candidate)
        if status != "MATCHED":
            raise ProviderError("Página encontrada sem metadados bibliográficos suficientes da obra original.")
        # HTML may contain navigation, reviews, references and paywall landing pages.
        # Only explicit publisher abstract metadata is eligible; full HTML is never presumed full text.
        abstract = (meta.get("citation_abstract") or meta.get("dc.description") or [""])[0]
        if not abstract:
            raise ProviderError("Página identificada, mas texto da obra não acessível de forma confiável.")
        return [page(plain_html(abstract), method="PUBLISHER_ABSTRACT")], "ABSTRACT_ONLY", [
            "Somente resumo identificado nos metadados da página da editora."
        ]

    def _preserve(self, request, source, data, content_type, limitations):
        """Files are exchanged through upload-service; no shared container paths."""
        endpoint = "/files" if content_type == "application/pdf" else "/artifacts"
        filename = "source.pdf" if content_type == "application/pdf" else "source.txt"
        try:
            with httpx.Client(timeout=self.settings.request_timeout, trust_env=False) as client:
                response = client.post(self.settings.upload_url.rstrip("/") + endpoint,
                    headers={"X-Internal-Token": self.settings.internal_token},
                    data={"user_id": request.user_id, "document_id": request.document_id,
                          "kind": "source", "idempotency_key": source["id"]},
                    files={"file": (filename, data, content_type)})
                response.raise_for_status()
                file_id = response.json()["file_id"]
            source["file_id"] = file_id
            source["details"] = {"file_id": file_id, "content_type": content_type}
        except (httpx.HTTPError, ValueError, KeyError):
            limitations.append("Não foi possível arquivar o arquivo-fonte; texto e hash precisam ser preservados no catálogo.")

    @staticmethod
    def _attempt(provider, query, url, result, **extra):
        return {"provider": provider, "query": query, "url": url, "result": result,
                "timestamp": datetime.now(timezone.utc).isoformat(), **extra}

"""Conservative bibliography-first parser. Ambiguity is data, not a forced match."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import re
import unicodedata
import uuid

from packages.document_processing.prose import paragraph_spans

YEAR = r"(?:18|19|20)\d{2}[a-z]?"
DATE = rf"(?:{YEAR}|s\.?\s*d\.?|n\.?\s*d\.?)"
HEAD = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?(?:refer[eê]ncias(?:\s+bibliogr[aá]ficas)?|bibliografia|references|bibliography)\s*[:.]?\s*$", re.I)
NEXT = re.compile(r"^\s*(?:(?:cap[ií]tulo|chapter|anexos?|ap[eê]ndices?|appendix)\b|(?:\d+(?:\.\d+)*\.?\s+)(?:introdu[cç][aã]o|introduction|metodologia|methods|resultados|results|discuss[aã]o|conclus[oõ]es)\b)", re.I)
NUMBERED = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.)])\s+")
ENTRY_AUTHOR = re.compile(r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]+,\s*[A-ZÀ-ÖØ-Þ]|^[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ\s'’-]{2,}[.;]")
DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
PARSER_VERSION = "conservative-rules-v2"
INITIAL_AUTHOR = re.compile(r"^(?:[^\W\d_]\.\s*-?\s*)+[^\W\d_][^\W\d_'’.-]*(?:[\w'’ .-]*[^\W\d_])$", re.U)


def _id(*parts) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(map(str, parts))))


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip()


def _year(value: str) -> tuple[int | None, str | None]:
    match = re.search(r"((?:18|19|20)\d{2})([a-z]?)", value)
    return (int(match[1]), match[2] or None) if match else (None, None)


def _numbered_authors(text: str):
    """IEEE-style comma-separated authors must not split at an initial's dot."""
    if re.match(rf"^[^()]+\(({DATE})\)\s*\.\s+", text, re.I):
        return None  # A numbered APA entry still uses its author-date delimiter.
    parts = text.split(",")
    authors = []
    for part in parts:
        candidate = part.strip()
        if INITIAL_AUTHOR.fullmatch(candidate):
            authors.append(candidate)
        elif authors and re.fullmatch(r"(?:and\s+)?et\s+al\.?", candidate, re.I):
            authors.append(candidate)
        else:
            break
    if authors and len(parts) > len(authors):
        return authors, ",".join(parts[len(authors):]).strip()
    # Corporate/byline references such as "OpenAI, Technical report, 2023".
    # ABNT surname/comma/given-name entries continue through their own parser.
    first = parts[0].strip()
    if (len(parts) >= 3 and 1 <= len(first.split()) <= 6 and "." not in first and ";" not in parts[1]
            and not (first.isupper() and "." in parts[1]) and not re.search(YEAR, first)):
        return [first], ",".join(parts[1:]).strip()
    return None


def _numbered_title(remainder: str) -> str:
    # Initial-first/IEEE references delimit publication data with commas. Dots
    # inside a title ("vs.", software names) do not terminate it; lower-case
    # comma continuations such as "security, privacy, and ethics" stay together.
    parts = remainder.split(",")
    title = [parts[0].strip()]
    publication = re.compile(rf"^(?:\(?{YEAR}\b|in\s*:|arxiv\b|https?://|doi\b|pp?\.|vol\.|no\.|\[online\])", re.I)
    for part in parts[1:]:
        value = part.strip()
        if not value or publication.match(value) or not value[0].islower():
            break
        title.append(value)
    return ", ".join(title).strip(" .")


def _parse_entry(raw: str, start_page: int, end_page: int, ordinal: int, scope: str) -> dict:
    numbered = NUMBERED.match(raw)
    number = int(numbered[1] or numbered[2]) if numbered else None
    text = re.sub(r"\s+", " ", raw[numbered.end():] if numbered else raw).strip()
    doi = DOI.search(text)
    url = re.search(r"https?://[^\s<>]+", text, re.I)
    isbn = re.search(r"\bISBN(?:-1[03])?\s*:?\s*([0-9Xx -]{10,20})", text, re.I)
    year_match = re.search(rf"\b{YEAR}\b", text)
    year, suffix = _year(year_match[0]) if year_match else (None, None)
    # APA years reliably delimit authors; ABNT uses surname/comma and first full stop.
    numeric = _numbered_authors(text) if numbered else None
    apa = re.match(rf"(.+?)\s*\(({DATE})\)\s*[.,]?\s*(.*)", text, re.I | re.S)
    if numeric:
        authors, remainder = numeric
        author_part = "; ".join(authors)
    elif apa:
        author_part, remainder = apa[1].strip(), apa[3].strip()
    else:
        split = re.match(r"(.+?)\.\s+(?=[A-ZÀ-ÖØ-Þ])(.+)", text, re.S)
        author_part, remainder = (split[1], split[2]) if split else ("", text)
    if not numeric:
        authors = [a.strip(" .") for a in re.split(r"\s*;\s*|\s+(?:&|and|e)\s+", author_part) if a.strip(" .")]
    title = (_numbered_title(remainder) if numeric else
             re.split(r"\.\s+|\s+doi\s*:|https?://", remainder, maxsplit=1, flags=re.I)[0].strip(" ."))
    if title and year_match and title == text:
        title = None
    issues = []
    if not authors:
        issues.append("MISSING_AUTHORS")
    if not title:
        issues.append("MISSING_TITLE")
    if year is None and not re.search(r"\bs\.?\s*d\.?\b", text, re.I):
        issues.append("MISSING_YEAR")
    metadata = {"parser": PARSER_VERSION, "enrichment": [],
                "author_institutional": author_part if author_part.isupper() and "," not in author_part else None,
                "venue": None, "publisher": None, "volume": None, "issue": None,
                "pages": None, "edition": None, "work_type": None,
                "unparsed_remainder": remainder, "ordinal": ordinal}
    return {"id": _id(scope, "reference", ordinal, start_page, raw), "raw_text": raw,
            "authors": authors, "title": title, "year": year, "year_suffix": suffix,
            "doi": doi[0].rstrip(".,;)").lower() if doi else None,
            "isbn": re.sub(r"\s", "", isbn[1]).rstrip("-") if isbn else None,
            "url": url[0].rstrip(".,;)") if url else None, "entry_number": number,
            "page_start": start_page, "page_end": end_page,
            "metadata": metadata, "issues": issues}


def _repeated_margins(pages: list[dict]) -> set[str]:
    occurrences = defaultdict(set)
    for page in pages:
        text = page.get("normalized_text", "")
        for block in page.get("blocks", []):
            bbox = block.get("bbox")
            value = text[block.get("offset_start", 0):block.get("offset_end", 0)].strip()
            if (bbox and bbox[1] < 80 and 0 < len(value) < 220
                    and not HEAD.fullmatch(value) and not NUMBERED.match(value)):
                occurrences[_fold(value)].add(page["physical_page_number"])
    return {value for value, numbers in occurrences.items() if len(numbers) >= 2}


def _bibliography_lines(page: dict, repeated_margins: set[str]):
    """Read columns in sequence, retaining each line's original text offsets.

    This view is local to cataloging: changing persisted page text would invalidate
    historical citation/chunk positions. A clear central gutter is required;
    ambiguous layouts retain their extracted order.
    """
    text = page.get("normalized_text", "")
    blocks = [block for block in page.get("blocks", [])
              if block.get("bbox") and isinstance(block.get("offset_start"), int)
              and isinstance(block.get("offset_end"), int)]
    # Use geometry only when it covers all non-whitespace text.
    covered = [False] * len(text)
    for block in blocks:
        for index in range(max(0, block["offset_start"]), min(len(text), block["offset_end"])):
            covered[index] = True
    if not blocks or any(not covered[i] and not char.isspace() for i, char in enumerate(text)):
        offset = 0
        for line in text.splitlines(keepends=True):
            yield line, offset, offset + len(line)
            offset += len(line)
        return
    left = min(block["bbox"][0] for block in blocks)
    right = max(block["bbox"][2] for block in blocks)
    width = right - left
    gutter = None
    best = 0
    for fraction in (.5, .45, .55, .4, .6):
        candidate = left + width * fraction
        before = [b for b in blocks if b["bbox"][2] < candidate - 3]
        after = [b for b in blocks if b["bbox"][0] > candidate + 3]
        score = len(before) + len(after)
        total_characters = sum(b["offset_end"] - b["offset_start"] for b in blocks)
        substantial_sides = all(sum(b["offset_end"] - b["offset_start"] for b in side) >= total_characters * .2
                                for side in (before, after))
        overlap = (min(max(b["bbox"][3] for b in side) for side in (before, after)) -
                   max(min(b["bbox"][1] for b in side) for side in (before, after))) if before and after else 0
        if (len(before) >= 2 and len(after) >= 2 and substantial_sides and overlap > 20
                and score >= len(blocks) * .7 and score > best):
            gutter, best = candidate, score
    if gutter is not None:
        spanning = sorted([b for b in blocks if b["bbox"][0] <= gutter + 3 and b["bbox"][2] >= gutter - 3],
                          key=lambda b: (b["bbox"][1], b["bbox"][0]))
        remaining = [b for b in blocks if b not in spanning]
        ordered = []
        for span in spanning + [None]:
            band = [b for b in remaining if span is None or b["bbox"][1] < span["bbox"][1]]
            ordered.extend(sorted(band, key=lambda b: (b["bbox"][0] >= gutter, b["bbox"][1], b["bbox"][0])))
            remaining = [b for b in remaining if b not in band]
            if span is not None:
                ordered.append(span)
        blocks = ordered
    else:
        blocks.sort(key=lambda b: b["offset_start"])
    for block in blocks:
        offset = block["offset_start"]
        content = text[offset:block["offset_end"]]
        if block.get("is_folio") or (block["bbox"][1] < 80 and _fold(content) in repeated_margins):
            continue
        for line in content.splitlines(keepends=True):
            yield line, offset, offset + len(line)
            offset += len(line)


def catalog_references(pages: list[dict], page_range=None, scope: str = "") -> dict:
    """Catalog first; optional inclusive physical page range supplies missing headings."""
    if page_range is not None:
        if isinstance(page_range, dict):
            page_range = (page_range.get("start", page_range.get("page_start")),
                          page_range.get("end", page_range.get("page_end")))
        if len(page_range) != 2 or not 1 <= int(page_range[0]) <= int(page_range[1]):
            raise ValueError("Intervalo bibliográfico inválido.")
    references, regions, warnings = [], [], []
    active = False
    entry_lines: list[str] = []
    entry_start = entry_end = None
    section_index = 0
    numbered_section = False
    repeated_margins = _repeated_margins(pages)

    def flush():
        nonlocal entry_lines, entry_start, entry_end
        if entry_lines:
            raw = "\n".join(entry_lines).strip()
            item = _parse_entry(raw, entry_start, entry_end, len(references), scope)
            item["metadata"]["bibliography_section"] = section_index
            references.append(item)
        entry_lines, entry_start, entry_end = [], None, None

    for page in sorted(pages, key=lambda p: p["physical_page_number"]):
        page_number = page["physical_page_number"]
        text = page.get("normalized_text", "")
        if page_range:
            selected = int(page_range[0]) <= page_number <= int(page_range[1])
            if not selected:
                flush()
                active = False
                continue
            active = True
        page_regions = []
        for line, offset, end in _bibliography_lines(page, repeated_margins):
            stripped = line.strip()
            if HEAD.fullmatch(stripped):
                flush()
                active = True
                numbered_section = False
                section_index += 1
                page_regions.append((offset, end))
                continue
            if active and not page_range and NEXT.match(stripped):
                flush()
                active = False
            if active:
                page_regions.append((offset, end))
                # Printed folios are not bibliographic entries.
                if re.fullmatch(r"(?:\d+|[ivxlcdmIVXLCDM]{1,12})", stripped):
                    continue
                if not stripped:
                    # Preserve blank lines but do not assume they end wrapped entries.
                    if entry_lines:
                        entry_lines.append("")
                else:
                    number = NUMBERED.match(stripped)
                    if (number or not numbered_section and ENTRY_AUTHOR.match(stripped)) and entry_lines:
                        flush()
                    if number:
                        numbered_section = True
                    if entry_start is None:
                        entry_start = page_number
                    entry_end = page_number
                    entry_lines.append(line.rstrip("\r\n"))
        # A column bibliography can be interleaved with body text in the stored
        # geometric order. Mask only its real intervals, never the intervening body.
        merged = []
        for start, end in sorted(page_regions):
            if merged and not text[merged[-1][1]:start].strip():
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        regions.extend({"page_number": page_number, "start": start, "end": end} for start, end in merged)
    flush()
    if not regions:
        warnings.append("Nenhuma seção bibliográfica localizada. Informe o intervalo físico de páginas; a bibliografia não foi inventada.")
    if regions and not references:
        warnings.append("Seção bibliográfica localizada sem entradas legíveis.")
    if references:
        warnings.append("Parser conservador: revise segmentação, autores institucionais, metadados ausentes e bibliografias por capítulo.")
    duplicate_keys = defaultdict(list)
    for ref in references:
        key = ref["doi"] or _fold(ref["raw_text"])
        duplicate_keys[key].append(ref)
    for group in duplicate_keys.values():
        if len(group) > 1:
            for ref in group:
                ref["issues"].append("DUPLICATE_REFERENCE")
                ref["metadata"]["duplicate_reference_ids"] = [r["id"] for r in group if r["id"] != ref["id"]]
    return {"references": references, "bibliography_regions": regions, "warnings": warnings,
            "status": "COMPLETED", "scope": scope, "parser_version": PARSER_VERSION}


def _author_keys(ref: dict) -> list[str]:
    keys = []
    for author in ref.get("authors", []):
        if isinstance(author, dict):
            author = author.get("family") or author.get("name") or ""
        author = re.sub(r"\bet\s+al\.?", "", author, flags=re.I).strip()
        key = author.split(",", 1)[0]
        if key:
            keys.append(_fold(key))
            words = key.split()
            institution = re.search(r"\b(?:organiza[cç][aã]o|organization|universidade|university|instituto|institute|minist[eé]rio|ministry|association|associa[cç][aã]o)\b", key, re.I)
            if len(words) > 1 and institution:
                keys.append(_fold("".join(word[0] for word in words if len(word) > 2)))
    for alias in ref.get("metadata", {}).get("author_aliases", []):
        if isinstance(alias, str) and alias.strip():
            keys.append(_fold(alias))
    return keys


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    # A full stop in "et al.", "s.d." or "p. 12" does not end a sentence here.
    before = [match for match in re.finditer(r"[.!?](?:[\"”']?)\s+(?=[A-ZÀ-ÖØ-Þ])|\n\s*\n", text)
              if match.end() <= start]
    after = re.search(r"[.!?](?:[\"”']?)(?=\s+[A-ZÀ-ÖØ-Þ]|\s*$)|\n\s*\n", text[end:])
    left = before[-1].end() if before else 0
    right = end + after.end() if after else len(text)
    return left, right


def map_citations(pages: list[dict], catalog: dict, scope: str = "") -> dict:
    if catalog.get("status") != "COMPLETED":
        raise ValueError("O catálogo bibliográfico deve estar concluído antes do mapeamento.")
    scope = scope or catalog.get("scope", "")
    refs = catalog["references"]
    ordered = sorted(pages, key=lambda p: p["physical_page_number"])
    regions_by_page = defaultdict(list)
    for region in catalog.get("bibliography_regions", []):
        regions_by_page[region["page_number"]].append(region)
    joined_parts, spans, prose_ranges = [], [], []
    cursor = 0
    for page in ordered:
        text = page.get("normalized_text", "")
        masked = ["\n" if char == "\n" else " " for char in text]
        excluded = [(region["start"], region["end"])
                    for region in regions_by_page[page["physical_page_number"]]]
        excluded += [(block["offset_start"], block["offset_end"])
                     for block in page.get("blocks", []) if block.get("is_folio")]
        for paragraph in paragraph_spans(page):
            parts = [(paragraph["offset_start"], paragraph["offset_end"])]
            for omitted_start, omitted_end in excluded:
                remaining = []
                for start, end in parts:
                    if omitted_start >= end or omitted_end <= start:
                        remaining.append((start, end))
                    else:
                        if start < omitted_start:
                            remaining.append((start, omitted_start))
                        if omitted_end < end:
                            remaining.append((omitted_end, end))
                parts = remaining
            for start, end in parts:
                while start < end and text[start].isspace():
                    start += 1
                while end > start and text[end - 1].isspace():
                    end -= 1
                if start < end:
                    masked[start:end] = text[start:end]
                    prose_ranges.append((cursor + start, cursor + end, page["physical_page_number"]))
        content = "".join(masked)
        spans.append((cursor, cursor + len(text), page, content))
        joined_parts.append(content)
        cursor += len(text) + 1
    joined = "\n".join(joined_parts)
    original_joined = "\n".join(page.get("normalized_text", "") for page in ordered)
    occurrences = []
    used_ranges = []
    by_number = defaultdict(list)
    for ref in refs:
        if ref.get("entry_number") is not None:
            by_number[int(ref["entry_number"])].append(ref)

    def location(position):
        for start, end, page, _ in reversed(spans):
            if position >= start:
                return page["physical_page_number"], max(0, min(position - start, end - start))
        return 1, 0

    def resolve_author(author, date):
        year, suffix = _year(date)
        # Match surname(s), et al. and institution aliases; do not disambiguate duplicates.
        cleaned_author = re.sub(r"\bet\s+al\.?", "", author, flags=re.I).strip(" .")
        cleaned = _fold(cleaned_author)
        tokens = [_fold(t) for t in re.split(r"\s+(?:e|and)\s+|&|,", cleaned_author, flags=re.I) if t.strip()]
        author_matches = [ref for ref in refs
                          if cleaned in _author_keys(ref)
                          or bool(tokens) and all(token in _author_keys(ref) for token in tokens)]
        matches = [ref for ref in author_matches if ref.get("year") == year and ref.get("year_suffix") == suffix]
        return matches, author_matches

    def add(start, end, kind, groups, metadata=None):
        if any(start < b and end > a for a, b in used_ranges):
            return
        containing = [(left, right, number) for left, right, number in prose_ranges
                      if left <= start < right or left < end <= right]
        if len(containing) == 1 and containing[0][0] <= start and end <= containing[0][1]:
            para_left, para_right, _ = containing[0]
        elif (len(containing) == 2 and containing[1][2] == containing[0][2] + 1
              and not original_joined[containing[0][1]:containing[1][0]].strip()):
            # Preserve a citation marker broken only by a physical page break.
            # Other paragraph boundaries never turn independent text into a citation.
            para_left, para_right = containing[0][0], containing[1][1]
        else:
            return
        used_ranges.append((start, end))
        resolved, candidates = [], []
        missing, ambiguous = False, False
        group_data = []
        for matches, fallback in groups:
            candidates.extend(r["id"] for r in matches or fallback)
            if len(matches) == 1:
                resolved.append(matches[0]["id"])
            elif len(matches) > 1:
                ambiguous = True
            else:
                missing = True
            group_data.append({"reference_ids": [r["id"] for r in matches],
                               "candidate_reference_ids": [r["id"] for r in fallback],
                               "status": "RESOLVED" if len(matches) == 1 else "AMBIGUOUS" if matches else "MISSING_REFERENCE"})
        state = "AMBIGUOUS" if ambiguous else "MISSING_REFERENCE" if missing else "RESOLVED"
        pstart, ostart = location(start)
        pend, oend = location(end - 1)
        paragraph = joined[para_left:para_right]
        left, right = _sentence_bounds(paragraph, start - para_left, end - para_left)
        sentence = re.sub(r"\s+", " ", paragraph[left:right]).strip()
        item_meta = {"groups": group_data, "cross_page": pstart != pend,
                     "start_position": {"page_number": pstart, "offset": ostart},
                     "end_position": {"page_number": pend, "offset": oend + 1},
                     "approximate_location": True, "claim_segmentation": "paragraphs-v1",
                     "paragraph_start_position": dict(zip(("page_number", "offset"), location(para_left))),
                     "paragraph_end_position": dict(zip(("page_number", "offset"), location(para_right))),
                     **(metadata or {})}
        if re.search(r"\bapud\b", sentence, re.I):
            item_meta.update(secondary_citation=True, secondary_scope="intermediary_only",
                             original_not_directly_verified=True)
        occurrences.append({"id": _id(scope, "occurrence", start, end, joined[start:end]),
                            "marker": joined[start:end], "page_start": pstart, "page_end": pend,
                            "offset_start": ostart, "offset_end": oend + 1,
                            "sentence": sentence, "paragraph": paragraph, "context": paragraph,
                            "reference_ids": sorted(set(resolved)),
                            "candidate_reference_ids": sorted(set(candidates)),
                            "binding_status": state, "kind": kind, "metadata": item_meta,
                            "_paragraph_key": (para_left, para_right)})

    # Parenthesized groups, including author-date and secondary citations.
    for match in re.finditer(r"\(([^()]{1,500})\)", joined):
        inner = match[1]
        if not re.search(DATE, inner, re.I):
            continue
        groups = []
        pieces = re.split(r";|\bapud\b", inner, flags=re.I)
        for piece in pieces:
            dates = list(re.finditer(DATE, piece, re.I))
            if not dates:
                continue
            author = piece[:dates[0].start()].strip(" ,\n")
            if not author or not re.search(r"[A-Za-zÀ-ÿ]{2}", author):
                continue
            for date in dates:
                groups.append(resolve_author(author, date[0]))
        if groups:
            add(match.start(), match.end(), "AUTHOR_DATE", groups)
    # Narrative forms use known catalog authors plus a conservative surname fallback.
    # Institution phrases take precedence over their final word.
    for ref in refs:
        for author in ref.get("authors", []):
            if not isinstance(author, str) or "," in author or len(author.split()) < 2:
                continue
            pattern = re.compile(rf"\b({re.escape(author)})\s*\(\s*({DATE})\s*\)", re.I)
            for match in pattern.finditer(joined):
                add(match.start(), match.end(), "AUTHOR_DATE", [resolve_author(match[1], match[2])])
    # Accent-insensitive matching is handled for binding, while this expression preserves ink.
    narrative = re.compile(rf"\b([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*(?:\s+(?:et\s+al\.?|(?:e|&|and)\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*))?)\s*\(\s*({DATE})(?:\s*,\s*p{1,2}\.?\s*\d+(?:[-–]\d+)?)?\s*\)")
    for match in narrative.finditer(joined):
        add(match.start(), match.end(), "AUTHOR_DATE",
            [resolve_author(match[1], match[2])])
    # Numerical brackets need bibliographic numbering AND nearby prose, not equations.
    for match in re.finditer(r"\[(\d{1,4}(?:\s*(?:[,;–-])\s*\d{1,4})*)\]", joined):
        prefix = joined[max(0, match.start() - 100):match.start()]
        if not by_number or not re.search(r"[A-Za-zÀ-ÿ]{3}", prefix):
            continue
        if re.search(r"(?:equa[cç][aã]o|equation|matriz|matrix|vetor|vector|intervalo|interval)\s*$", prefix, re.I):
            continue
        numbers = []
        for segment in re.split(r"[,;]", match[1]):
            bounds = re.split(r"[-–]", segment.strip())
            if len(bounds) == 2:
                low, high = map(int, bounds)
                if high < low or high - low > 100:
                    continue
                numbers.extend(range(low, high + 1))
            else:
                numbers.append(int(segment))
        if numbers:
            add(match.start(), match.end(), "NUMERIC",
                [(by_number.get(n, []), []) for n in numbers], {"numbers": numbers})
    superscript_map = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    for match in re.finditer(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", joined):
        if by_number and match.start() > 0 and re.search(r"[A-Za-zÀ-ÿ.,!?)]", joined[match.start()-1]):
            number = int(match[0].translate(superscript_map))
            if number in by_number:
                add(match.start(), match.end(), "SUPERSCRIPT", [(by_number[number], [])], {"numbers": [number]})
    occurrences.sort(key=lambda o: (o["page_start"], o["offset_start"]))
    claims_by_paragraph = {}
    for occurrence in occurrences:
        key = occurrence.pop("_paragraph_key")
        if key not in claims_by_paragraph:
            claims_by_paragraph[key] = {"id": _id(scope, "claim", "paragraphs-v1", *key),
                                      "text": occurrence["paragraph"], "page_start": location(key[0])[0],
                                      "page_end": location(key[1] - 1)[0], "occurrence_ids": [], "reference_ids": [],
                                      "metadata": {"segmentation": "paragraphs-v1", "requires_human_review": True}}
        claim = claims_by_paragraph[key]
        claim["occurrence_ids"].append(occurrence["id"])
        claim["reference_ids"] = sorted(set(claim["reference_ids"] + occurrence["reference_ids"]))
        occurrence["claim_ids"] = [claim["id"]]
    coverage = []
    for _, _, page, body in spans:
        count = sum(o["page_start"] <= page["physical_page_number"] <= o["page_end"] for o in occurrences)
        extraction = page.get("status", "EXTRACTED")
        if extraction in {"UNVERIFIABLE", "FAILED"}:
            status = "UNVERIFIABLE"
        elif extraction == "PARTIALLY_READABLE":
            status = "PARTIALLY_READABLE"
        elif regions_by_page[page["physical_page_number"]] and not body.strip():
            status = "BIBLIOGRAPHIC"
        elif count:
            status = "ANALYZED"
        else:
            status = "NO_CITATIONS"
        coverage.append({**page, "extraction_status": extraction, "status": status,
                         "citation_count": count, "mapping_complete": True})
    cited = {rid for o in occurrences for rid in o["reference_ids"]}
    inverse = {"uncited_reference_ids": [r["id"] for r in refs if r["id"] not in cited],
               "missing_reference_occurrence_ids": [o["id"] for o in occurrences if o["binding_status"] == "MISSING_REFERENCE"],
               "ambiguous_occurrence_ids": [o["id"] for o in occurrences if o["binding_status"] == "AMBIGUOUS"],
               "duplicate_reference_ids": [r["id"] for r in refs if "DUPLICATE_REFERENCE" in r.get("issues", [])]}
    warnings = ["Detecção por regras em texto corrido; ausência de ocorrência detectada não implica erro. Cada trecho corresponde a um parágrafo completo e pode conter várias afirmações que exigem revisão.",
                "Sobrescritos Unicode são reconhecidos; sobrescritos representados apenas por posição gráfica podem exigir revisão humana."]
    if any(o["metadata"].get("secondary_citation") for o in occurrences):
        warnings.append("Citações secundárias (apud): a obra original não é considerada diretamente consultada.")
    return {"pages": coverage, "occurrences": occurrences, "claims": list(claims_by_paragraph.values()),
            "warnings": warnings, "inverse": inverse, "status": "COMPLETED"}

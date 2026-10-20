"""Page-local prose paragraphs without changing the bibliographic text or offsets.

Layout classification is conservative and geometric, not a visual-language model.
Persisted spans are authoritative, including an empty list for non-prose pages.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from statistics import median
import unicodedata

VERSION = "paragraphs-v1"
_CAPTION = re.compile(
    r"^(?:fig(?:ure|ura)?\.?|table|tabela|quadro|gr[aá]fico|diagrama)\s*\d+[a-z]?"
    r"(?:\s*[:.\-–—]|\s+\(|\s*$)", re.I)
_HEADING = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:abstract|resumo|introdu[cç][aã]o|introduction|"
    r"conclus(?:ion|ions|[aã]o|[oõ]es)|references|refer[eê]ncias(?: bibliogr[aá]ficas)?|"
    r"bibliograf[ií]a|bibliography|acknowledg(?:e)?ments|agradecimentos|"
    r"methods?|metodologia|resultados|results|discussion|discuss[aã]o)\s*[:.]?$", re.I)
_FOLIO = re.compile(r"^(?:\d{1,5}|[ivxlcdmIVXLCDM]{1,12})$")
_SENTENCE_END = re.compile(r'[.!?;:][”"\')\]]*$')


def _clean(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _valid_spans(text, spans):
    result, previous_end = [], 0
    for span in spans:
        start, end = span.get("offset_start"), span.get("offset_end")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise ValueError("Intervalo de parágrafo inválido para normalized_text.")
        if start < previous_end:
            raise ValueError("Parágrafos sobrepostos ou fora de ordem.")
        if text[start:end].strip():
            result.append({"offset_start": start, "offset_end": end})
        previous_end = end
    return result


def _prose(text):
    text = _clean(text)
    if not text or _FOLIO.fullmatch(text) or _HEADING.fullmatch(text) or _CAPTION.match(text):
        return False
    if re.match(r"^\s*(?:\|.*\||[-+|]{3,})\s*$", text):
        return False
    words = re.findall(r"\b[^\W\d_]+(?:[-’'][^\W\d_]+)*\b", text, re.UNICODE)
    letters = sum(char.isalpha() for char in text)
    if letters < max(3, len(text) * .35):
        return False
    # Short complete sentences are prose; isolated diagram labels are not.
    return len(words) >= 4 or (len(words) >= 2 and bool(_SENTENCE_END.search(text)))


def _overlap(box, region):
    width = max(0.0, min(box[2], region[2]) - max(box[0], region[0]))
    height = max(0.0, min(box[3], region[3]) - max(box[1], region[1]))
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    return width * height / area


def enrich_native_lines(pdf_page, blocks, text):
    """Add font/line/word geometry to existing blocks, leaving text byte-for-byte."""
    import pymupdf

    dictionary = pdf_page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES, sort=True)
    by_box = {tuple(round(value, 2) for value in block["bbox"]): block
              for block in dictionary["blocks"] if block.get("type") == 0}
    words = defaultdict(list)
    for word in pdf_page.get_text("words"):
        words[(word[5], word[6])].append({"bbox": list(word[:4]), "text": word[4]})
    for block in blocks:
        original = by_box.get(tuple(round(value, 2) for value in block["bbox"]), {})
        cursor = block["offset_start"]
        lines = []
        for number, line in enumerate(original.get("lines", [])):
            value = _clean("".join(span.get("text", "") for span in line.get("spans", [])))
            if not value:
                continue
            start = text.find(value, cursor, block["offset_end"])
            if start < 0:
                continue
            fonts = [span.get("size", 10) for span in line.get("spans", []) if span.get("text", "").strip()]
            lines.append({
                "offset_start": start, "offset_end": start + len(value), "bbox": list(line["bbox"]),
                "font_size": median(fonts) if fonts else 10,
                "bold": all(span.get("flags", 0) & 16 for span in line.get("spans", [])),
                "words": words.get((original.get("number"), number), []),
            })
            cursor = start + len(value)
        if lines:
            block["lines"] = lines


def _units(page):
    text, units = page.get("normalized_text", ""), []
    for index, block in enumerate(page.get("blocks", [])):
        lines = block.get("lines") or [block]
        expanded = []
        for line in lines:
            start, end = line.get("offset_start"), line.get("offset_end")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text) and "\n\n" in text[start:end]:
                for part in re.finditer(r"\S.*?(?=\n\s*\n|\Z)", text[start:end], re.S):
                    expanded.append({**line, "offset_start": start + part.start(),
                                     "offset_end": start + part.end()})
            else:
                expanded.append(line)
        for line in expanded:
            start, end = line.get("offset_start"), line.get("offset_end")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
                continue
            unit = {**line, "block_id": index, "text": text[start:end],
                    "is_folio": block.get("is_folio", False), "paragraph_id": block.get("paragraph_id"),
                    "excluded": block.get("excluded_kind") or line.get("excluded_kind")}
            units.append(unit)
    return sorted(units, key=lambda unit: unit["offset_start"])


def _table_units(units):
    """Repeated short cells aligned in rows; works for OCR and unruled native tables."""
    rows = []
    for index, unit in sorted(enumerate(units), key=lambda pair: ((pair[1].get("bbox") or [0, 0, 0, 0])[1],
                                                                 (pair[1].get("bbox") or [0, 0, 0, 0])[0])):
        box = unit.get("bbox")
        if not box:
            continue
        center = (box[1] + box[3]) / 2
        row = next((row for row in rows[-3:] if abs(row["center"] - center) < 4), None)
        if row is None:
            row = {"center": center, "units": [], "cells": []}
            rows.append(row)
        row["units"].append(index)
        words = sorted(unit.get("words", []), key=lambda word: word["bbox"][0])
        if words:
            cells = []
            for word in words:
                if not cells or word["bbox"][0] - cells[-1]["right"] > max(18, unit.get("font_size", 10) * 1.8):
                    cells.append({"left": word["bbox"][0], "right": word["bbox"][2], "text": word["text"]})
                else:
                    cells[-1]["right"] = word["bbox"][2]
                    cells[-1]["text"] += " " + word["text"]
            row["cells"].extend(cells)
        else:
            row["cells"].append({"left": box[0], "right": box[2], "text": _clean(unit["text"])})
    candidates = []
    for row in rows:
        cells = sorted(row["cells"], key=lambda cell: cell["left"])
        if len(cells) < 2 or any(len(cell["text"].split()) > 8 for cell in cells):
            continue
        # Two columns of complete sentences are body columns, not a table.
        if all(_SENTENCE_END.search(cell["text"]) for cell in cells):
            continue
        row["cells"] = cells
        candidates.append(row)
    excluded = set()
    for index, row in enumerate(candidates):
        family = [row]
        for other in candidates[index + 1:index + 8]:
            if other["center"] - family[-1]["center"] > 45:
                break
            if len(other["cells"]) == len(row["cells"]) and all(
                abs(a["left"] - b["left"]) <= 12 for a, b in zip(row["cells"], other["cells"])
            ):
                family.append(other)
        has_data = any(re.search(r"\d", cell["text"]) for item in family for cell in item["cells"])
        if len(family) >= 3 and (has_data or len(row["cells"]) >= 3):
            excluded.update(unit for item in family for unit in item["units"])
    return excluded


def _paragraphs_from_units(page, regions=()):
    text, units = page.get("normalized_text", ""), _units(page)
    if not units:
        return _fallback_paragraphs(text), []
    if not any(unit.get("bbox") for unit in units):
        spans = []
        for unit in units:
            if unit.get("excluded") or unit.get("is_folio"):
                continue
            start, end = unit["offset_start"], unit["offset_end"]
            spans.extend({"offset_start": start + span["offset_start"], "offset_end": start + span["offset_end"]}
                         for span in _fallback_paragraphs(text[start:end]))
        return _valid_spans(text, spans), []
    sizes = [unit.get("font_size", 10) for unit in units if len(unit["text"]) > 35]
    body_size = median(sizes) if sizes else 10
    table_units = _table_units(units)
    groups, excluded = [], []
    current, previous = None, None
    caption_blocks, caption_paragraphs = set(), set()
    for index, unit in enumerate(units):
        value, box = _clean(unit["text"]), unit.get("bbox")
        kind = unit.get("excluded")
        if unit.get("is_folio") or _FOLIO.fullmatch(value):
            kind = "folio"
        elif _CAPTION.match(value):
            kind = "caption"
            caption_blocks.add(unit["block_id"])
            if unit.get("paragraph_id"):
                caption_paragraphs.add(tuple(unit["paragraph_id"]))
        elif _HEADING.fullmatch(value):
            kind = "heading"
        elif len(value.split()) <= 14 and not _SENTENCE_END.search(value) and (
            unit.get("bold") or unit.get("font_size", body_size) > body_size * 1.15
            or (value.isupper() and len(value) < 100)
        ):
            kind = "heading"
        if index in table_units:
            kind = "table"
        if unit["block_id"] in caption_blocks or (unit.get("paragraph_id") and tuple(unit["paragraph_id"]) in caption_paragraphs):
            kind = "caption"
        if box:
            for region in regions:
                if _overlap(box, region["bbox"]) >= .35:
                    kind = region["kind"]
                    break
        if kind:
            excluded.append({"offset_start": unit["offset_start"], "offset_end": unit["offset_end"], "kind": kind})
            current, previous = None, None
            continue
        start, end = unit["offset_start"], unit["offset_end"]
        split = current is None
        if previous and current:
            between = text[previous["offset_end"]:start]
            split = bool(between.strip()) or "\n\n" in between
            previous_box = previous.get("bbox")
            if unit.get("paragraph_id") and previous.get("paragraph_id"):
                split |= unit["paragraph_id"] != previous["paragraph_id"]
            if box and previous_box:
                height = max(6, min(box[3] - box[1], previous_box[3] - previous_box[1]))
                gap = box[1] - previous_box[3]
                split |= gap > height * .8 or gap < -height * .5
                split |= box[0] > previous_box[2] or box[2] < previous_box[0]
                # An indented line after a completed sentence starts a new paragraph.
                split |= box[0] - previous_box[0] > body_size * .8 and bool(_SENTENCE_END.search(_clean(previous["text"])))
        if split:
            current = {"offset_start": start, "offset_end": end}
            groups.append(current)
        else:
            current["offset_end"] = end
        previous = unit
    spans = []
    for group in groups:
        if _prose(text[group["offset_start"]:group["offset_end"]]):
            spans.append(group)
        else:
            excluded.append({**group, "kind": "non_prose"})
    return _valid_spans(text, spans), sorted(excluded, key=lambda span: span["offset_start"])


def _fallback_paragraphs(text):
    """Legacy/HTML text: keep blank-separated paragraphs, reject explicit non-prose."""
    spans, current = [], None
    caption, previous_end = False, 0
    for match in re.finditer(r"[^\n]+(?:\n|$)", text):
        value = match.group().strip()
        start = match.start() + len(match.group()) - len(match.group().lstrip())
        end = match.start() + len(match.group().rstrip())
        if "\n\n" in text[previous_end:start]:
            caption = False
        caption = caption or bool(_CAPTION.match(value))
        previous_end = end
        reject = caption or _HEADING.fullmatch(value) or _FOLIO.fullmatch(value)
        reject = reject or value.count("|") >= 2 or bool(re.fullmatch(r"[-+=| ]{3,}", value))
        # Tab-separated or fixed-width short-cell rows remain detectable in legacy text.
        cells = re.split(r"\t+| {3,}", value)
        reject = reject or (len(cells) >= 3 and all(len(cell.split()) <= 5 for cell in cells))
        if reject:
            current = None
            continue
        if current is None or "\n\n" in text[current["offset_end"]:start]:
            current = {"offset_start": start, "offset_end": end}
            spans.append(current)
        else:
            current["offset_end"] = end
    return _valid_spans(text, [span for span in spans if _prose(text[span["offset_start"]:span["offset_end"]])])


def paragraph_spans(page: dict) -> list[dict]:
    text = page.get("normalized_text", "")
    quality = page.get("quality")
    if not isinstance(quality, dict):
        quality = {}
    if quality.get("prose_filter_version") == VERSION:
        return _valid_spans(text, quality.get("prose_paragraphs", []))
    if page.get("blocks"):
        return _paragraphs_from_units(page)[0]
    return _fallback_paragraphs(text)


def raster_grid_regions(image, scale):
    """Detect ruled regions in a scan using long intersecting dark pixel runs."""
    from PIL import Image

    ratio = min(1.0, 900 / max(image.width, image.height))
    gray = image.convert("L").resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                                    Image.Resampling.BILINEAR)
    width, height = gray.size
    pixels = gray.tobytes()
    lines = []
    for horizontal in (True, False):
        outer, inner = (height, width) if horizontal else (width, height)
        minimum = max(24, int(inner * (.08 if horizontal else .035)))
        for coordinate in range(outer):
            start = None
            for position in range(inner + 1):
                dark = position < inner and pixels[coordinate * width + position if horizontal else position * width + coordinate] < 100
                if dark and start is None:
                    start = position
                elif not dark and start is not None:
                    if position - start >= minimum:
                        # Merge thick adjacent rules before building the intersection graph.
                        if not lines or lines[-1]["horizontal"] != horizontal or abs(lines[-1]["coordinate"] - coordinate) > 2 or abs(lines[-1]["start"] - start) > 3:
                            lines.append({"horizontal": horizontal, "coordinate": coordinate, "start": start, "end": position})
                    start = None
    if len(lines) > 600:
        raise ValueError("Raster muito complexo para detectar grades com segurança.")
    parents = list(range(len(lines)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for i, first in enumerate(lines):
        if not first["horizontal"]:
            continue
        for j, second in enumerate(lines):
            if second["horizontal"]:
                continue
            if first["start"] - 3 <= second["coordinate"] <= first["end"] + 3 and second["start"] - 3 <= first["coordinate"] <= second["end"] + 3:
                parents[root(j)] = root(i)
    groups = defaultdict(list)
    for index, line in enumerate(lines):
        groups[root(index)].append(line)
    regions = []
    factor = scale * ratio
    for group in groups.values():
        horizontal = [line for line in group if line["horizontal"]]
        vertical = [line for line in group if not line["horizontal"]]
        if len(horizontal) >= 2 and len(vertical) >= 2:
            box = [min(line["coordinate"] for line in vertical), min(line["coordinate"] for line in horizontal),
                   max(line["coordinate"] for line in vertical), max(line["coordinate"] for line in horizontal)]
            regions.append({"bbox": [value / factor for value in box], "kind": "raster_grid"})
    return regions


def analyze_prose(pdf_page, page):
    """Attach paragraph spans and diagnostics for native/OCR content."""
    regions, warnings = [], []
    try:
        for table in pdf_page.find_tables(strategy="lines_strict").tables:
            if table.row_count >= 2 and table.col_count >= 2:
                regions.append({"bbox": list(table.bbox), "kind": "table"})
                header = getattr(table, "header", None)
                if header and getattr(header, "external", False):
                    regions.append({"bbox": list(header.bbox), "kind": "table_header"})
    except Exception as exc:
        warnings.append(f"Detecção geométrica de tabelas indisponível ({type(exc).__name__}).")
    try:
        for image in pdf_page.get_image_info():
            box = image["bbox"]
            area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
            # Full-page scans/backgrounds contain the prose, not an exclusion region.
            if area < pdf_page.rect.get_area() * .7:
                regions.append({"bbox": list(box), "kind": "image"})
    except Exception as exc:
        warnings.append(f"Localização de imagens indisponível ({type(exc).__name__}).")
    try:
        drawings = pdf_page.get_drawings()
        for box in pdf_page.cluster_drawings(drawings=drawings, x_tolerance=8, y_tolerance=8):
            if box.width < 20 or box.height < 15 or box.get_area() > pdf_page.rect.get_area() * .7:
                continue
            intersecting = [drawing for drawing in drawings if drawing["rect"].intersects(box)]
            if len(intersecting) >= 2 or any(item[0] in {"re", "qu", "c"} for drawing in intersecting for item in drawing["items"]):
                regions.append({"bbox": list(box), "kind": "diagram"})
    except Exception as exc:
        warnings.append(f"Localização de diagramas indisponível ({type(exc).__name__}).")
    for block in page.get("blocks", []):
        regions.extend(block.get("raster_exclusions", []))
        if block.get("layout_warning"):
            warnings.append(block["layout_warning"])
    spans, excluded = _paragraphs_from_units(page, regions)
    return {
        "prose_filter_version": VERSION, "prose_paragraphs": spans,
        "prose_excluded_spans": excluded, "prose_excluded_counts": dict(Counter(span["kind"] for span in excluded)),
        "prose_regions": regions, "prose_filter_warnings": list(dict.fromkeys(warnings)),
    }

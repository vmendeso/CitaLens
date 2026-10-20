"""Bounded PDF validation and extraction; failed pages remain observable."""
from __future__ import annotations

import hashlib
import io
import re
import time
import unicodedata
from pathlib import Path

import pymupdf

from packages.document_processing.prose import VERSION, analyze_prose, enrich_native_lines, raster_grid_regions


class PDFValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def normalize_text(text: str) -> str:
    # Preserve lines/paragraphs for bibliography segmentation and local offsets.
    text = unicodedata.normalize("NFC", text).replace("\x00", "").replace("\r\n", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def validate_pdf(data: bytes, filename: str, content_type: str,
                 max_bytes: int = 30 * 1024 * 1024, max_pages: int = 500) -> dict:
    if Path(filename).suffix.lower() != ".pdf":
        raise PDFValidationError("INVALID_EXTENSION", "Envie um arquivo com extensão .pdf.")
    if content_type.split(";", 1)[0].strip().lower() != "application/pdf":
        raise PDFValidationError("INVALID_CONTENT_TYPE", "O tipo de conteúdo deve ser application/pdf.")
    if not data or len(data) > max_bytes:
        raise PDFValidationError("FILE_SIZE_LIMIT", "Arquivo vazio ou maior que o limite permitido.")
    if not data.startswith(b"%PDF-"):
        raise PDFValidationError("INVALID_PDF_SIGNATURE", "O arquivo não contém uma assinatura PDF válida.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise PDFValidationError("PASSWORD_PROTECTED", "PDF protegido por senha; envie uma cópia desbloqueada.")
            if document.page_count < 1:
                raise PDFValidationError("EMPTY_PDF", "O PDF não contém páginas.")
            if document.page_count > max_pages:
                raise PDFValidationError("PAGE_LIMIT", f"O PDF excede o limite de {max_pages} páginas.")
            # MuPDF can repair damaged documents. Preserve and disclose that fact.
            return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                    "page_count": document.page_count, "repaired": bool(document.is_repaired)}
    except PDFValidationError:
        raise
    except Exception as exc:
        raise PDFValidationError("CORRUPT_PDF", "O PDF está corrompido ou não pode ser lido.") from exc


def _label(page, blocks: list[dict]) -> str | None:
    # A PDF page label is a publisher assertion, not necessarily printed ink.
    for block in blocks:
        if block["bbox"][1] < page.rect.height * .13 or block["bbox"][3] > page.rect.height * .87:
            for line in block["text"].splitlines():
                if re.fullmatch(r"\s*(?:\d{1,5}|[ivxlcdmIVXLCDM]{1,12})\s*", line):
                    return line.strip()
    return page.get_label() or None


def _native(page) -> tuple[str, list[dict]]:
    blocks = []
    texts = []
    offset = 0
    for item in page.get_text("blocks", sort=True):
        if item[6] != 0:
            continue
        text = item[4]
        normalized = normalize_text(text)
        if not normalized:
            continue
        separator = ""
        if blocks:
            previous = blocks[-1]["bbox"]
            gap = item[1] - previous[3]
            line_height = max(8.0, min(previous[3] - previous[1], item[3] - item[1]))
            # MuPDF may emit each visually wrapped line as its own block.
            # Only larger vertical gaps create paragraph boundaries.
            separator = "\n" if -2 <= gap <= line_height * .7 else "\n\n"
            offset += len(separator)
        is_folio = (item[1] < page.rect.height * .13 or item[3] > page.rect.height * .87) and bool(
            re.fullmatch(r"(?:\d{1,5}|[ivxlcdmIVXLCDM]{1,12})", normalized))
        blocks.append({"bbox": list(item[:4]), "order": len(blocks), "text": text,
                       "offset_start": offset, "offset_end": offset + len(normalized),
                       "coordinate_space": "pdf_points", "approximate": False,
                       "is_folio": is_folio})
        texts.append(separator + normalized)
        offset += len(normalized)
    text = "".join(texts)
    try:
        enrich_native_lines(page, blocks, text)
    except Exception as exc:
        if blocks:
            blocks[0]["layout_warning"] = f"Geometria de linhas indisponível ({type(exc).__name__})."
    return text, blocks


def _ocr(page, languages: str, timeout: int, max_pixels: int) -> tuple[str, list[dict], float]:
    import pytesseract
    from PIL import Image

    scale = min(2.5, (max_pixels / max(page.rect.width * page.rect.height, 1)) ** .5)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    values = pytesseract.image_to_data(image, lang=languages,
                                      output_type=pytesseract.Output.DICT, timeout=timeout)
    lines: dict[tuple, list[int]] = {}
    confidence = []
    for index, word in enumerate(values["text"]):
        if not word.strip():
            continue
        key = (values["block_num"][index], values["par_num"][index], values["line_num"][index])
        lines.setdefault(key, []).append(index)
        value = float(values["conf"][index])
        if value >= 0:
            confidence.append(value / 100)
    text_parts = []
    blocks = []
    offset = 0
    for key, indices in lines.items():
        text = normalize_text(" ".join(values["text"][i] for i in indices))
        left = min(values["left"][i] for i in indices) / scale
        top = min(values["top"][i] for i in indices) / scale
        right = max(values["left"][i] + values["width"][i] for i in indices) / scale
        bottom = max(values["top"][i] + values["height"][i] for i in indices) / scale
        if text_parts:
            offset += 1
        blocks.append({"bbox": [left, top, right, bottom], "order": len(blocks), "text": text,
                       "offset_start": offset, "offset_end": offset + len(text),
                       "coordinate_space": "pdf_points", "approximate": True,
                       "paragraph_id": [key[0], key[1]], "line_id": key[2],
                       "font_size": max(6.0, (bottom - top) * .8),
                       "words": [{"text": values["text"][i],
                                  "bbox": [values["left"][i] / scale, values["top"][i] / scale,
                                           (values["left"][i] + values["width"][i]) / scale,
                                           (values["top"][i] + values["height"][i]) / scale]}
                                 for i in indices],
                       "is_folio": (top < page.rect.height * .13 or bottom > page.rect.height * .87)
                                   and bool(re.fullmatch(r"(?:\d{1,5}|[ivxlcdmIVXLCDM]{1,12})", text))})
        text_parts.append(text)
        offset += len(text)
    if blocks:
        try:
            blocks[0]["raster_exclusions"] = raster_grid_regions(image, scale)
        except Exception as exc:
            blocks[0]["layout_warning"] = f"Detecção de grades na imagem indisponível ({type(exc).__name__})."
    return "\n".join(text_parts), blocks, sum(confidence) / len(confidence) if confidence else 0.0


def extract_pdf(data: bytes, languages: str = "por+eng", ocr_enabled: bool = True,
                ocr_timeout: int = 40, max_pixels: int = 16_000_000,
                min_native_chars: int = 35, max_pages: int = 500,
                max_seconds: float = 840) -> dict:
    deadline = time.monotonic() + max(0, max_seconds)
    metadata = validate_pdf(data, "document.pdf", "application/pdf",
                            max_bytes=max(len(data), 1), max_pages=max_pages)
    pages = []
    warnings = ["PDF reparado automaticamente durante a leitura."] if metadata["repaired"] else []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for index, page in enumerate(document):
            result = {"physical_index": index, "physical_page_number": index + 1,
                      "printed_label": None, "raw_text": "", "normalized_text": "",
                      "extraction_method": "NONE", "blocks": [], "quality": {},
                      "status": "UNVERIFIABLE", "error": None}
            if time.monotonic() >= deadline:
                result["error"] = "Limite total de extração atingido; página não verificada."
                if result["error"] not in warnings:
                    warnings.append(result["error"])
                pages.append(result)
                continue
            try:
                text, blocks = _native(page)
                result.update(raw_text="\n".join(b["text"] for b in blocks), normalized_text=text,
                              blocks=blocks, extraction_method="NATIVE",
                              printed_label=_label(page, blocks))
                bad_fraction = text.count("\ufffd") / max(len(text), 1)
                # Native text can coexist with a scanned page area.
                image_area = sum(pymupdf.Rect(i["bbox"]).get_area()
                                 for i in page.get_image_info())
                image_heavy = image_area > page.rect.get_area() * .35
                needs_ocr = len(re.sub(r"\s", "", text)) < min_native_chars or bad_fraction > .02 or image_heavy
                quality = {"characters": len(text), "replacement_fraction": bad_fraction,
                           "ocr_confidence": None, "mixed_image_content": image_heavy,
                           "reading_order": "geometric", "offset_basis": "normalized_text"}
                result["quality"] = quality
                if needs_ocr and ocr_enabled:
                    try:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Extraction deadline exceeded")
                        ocr_text, ocr_blocks, confidence = _ocr(page, languages, min(ocr_timeout, remaining), max_pixels)
                        quality["ocr_confidence"] = round(confidence, 4)
                        if len(ocr_text) >= len(text) or bad_fraction > .02:
                            text, blocks = ocr_text, ocr_blocks
                            result.update(raw_text=text, normalized_text=text, blocks=blocks,
                                          extraction_method="OCR", printed_label=_label(page, blocks))
                        result["status"] = "EXTRACTED" if text.strip() and confidence >= .55 else "PARTIALLY_READABLE"
                    except Exception as exc:
                        result["status"] = "PARTIALLY_READABLE" if text.strip() else "UNVERIFIABLE"
                        result["error"] = f"OCR indisponível ou falhou ({type(exc).__name__})."
                        warnings.append(f"Página {index + 1}: {result['error']}")
                elif needs_ocr:
                    result["status"] = "PARTIALLY_READABLE" if text.strip() else "UNVERIFIABLE"
                    result["error"] = "OCR necessário e desabilitado."
                else:
                    result["status"] = "EXTRACTED"
                if not text.strip():
                    result["status"] = "UNVERIFIABLE"
                    result["error"] = result["error"] or "Nenhum texto extraível; página vazia ou ilegível."
                quality["characters"] = len(text)
                try:
                    quality.update(analyze_prose(page, result))
                    for warning in quality["prose_filter_warnings"]:
                        warnings.append(f"Página {index + 1}: {warning}")
                except Exception as exc:
                    # Preserve text for bibliography/review, but do not treat unfiltered
                    # tables/figures as prose when structural analysis fails.
                    quality.update(prose_filter_version=VERSION, prose_paragraphs=[],
                                   prose_filter_error=type(exc).__name__)
                    result["status"] = "PARTIALLY_READABLE" if text.strip() else "UNVERIFIABLE"
                    warnings.append(f"Página {index + 1}: falha na delimitação de texto corrido ({type(exc).__name__}).")
            except Exception as exc:
                result["error"] = f"Falha de extração na página ({type(exc).__name__})."
                warnings.append(f"Página {index + 1}: {result['error']}")
            pages.append(result)
    return {"pages": pages, "warnings": warnings, "metadata": metadata}

"""Bilingual (EN/HI) PaddleOCR extraction and Indian document field parser."""

import inspect
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from paddleocr import PaddleOCR
    HAS_PADDLE_OCR = True
except (ImportError, Exception):
    PaddleOCR = None
    HAS_PADDLE_OCR = False

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", re.IGNORECASE)
_IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", re.IGNORECASE)
_AADHAAR_STRICT_RE = re.compile(r"(?<!\d)([2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4})(?![\s-]?\d)")
_AADHAAR_RE = re.compile(r"(?<!\d)(\d{4}[\s-]?\d{4}[\s-]?\d{4})(?![\s-]?\d)")
_ACCOUNT_CANDIDATE_RE = re.compile(r"(?<!\d)(\d[\d\s-]{7,20}\d)(?!\d)")
_DOB_FULL_RE = re.compile(
    r"\b((?:0?[1-9]|[12][0-9]|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:19|20)\d{2}"
    r"|(?:19|20)\d{2}[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12][0-9]|3[01]))\b"
)
_DOB_YOB_RE = re.compile(
    r"(?:yob|year\s*of\s*birth|जन्म\s*वर्ष)\s*[:\-]?\s*((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_ROLL_NO_RE = re.compile(
    r"(?:roll\s*(?:no\.?|number)|रोल\s*(?:नं\.?|नंबर))\s*[:\-#]?\s*([A-Z0-9][A-Z0-9\/-]{2,19})\b",
    re.IGNORECASE,
)
_ANNUAL_INCOME_RE = re.compile(
    r"(?:rs\.?|inr|₹|income|आय)?\s*[:\-]?\s*(?:rs\.?|inr|₹)?\s*([0-9]{1,3}(?:,[0-9]{2,3})+|[0-9]{4,9})(?:\.\d{1,2})?",
    re.IGNORECASE,
)

_EXPLICIT_NAME_LABEL_RE = re.compile(
    r"^\s*(?P<label>(?:applicant(?:'s)?|candidate(?:'s)?|student(?:'s)?|"
    r"account\s*holder(?:'s)?|holder(?:'s)?|beneficiary|full)?\s*name|"
    r"आवेदक\s*का\s*नाम|छात्र\s*का\s*नाम|प्रार्थी\s*का\s*नाम|नाम)"
    r"\s*[:\-–]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_EXPLICIT_NAME_ONLY_RE = re.compile(
    r"^\s*(?:applicant(?:'s)?\s*name|candidate(?:'s)?\s*name|student(?:'s)?\s*name|"
    r"full\s*name|name|आवेदक\s*का\s*नाम|नाम)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
_RELATION_OR_ENTITY_LABEL_TOKENS = (
    "father",
    "mother",
    "husband",
    "wife",
    "guardian",
    "spouse",
    "son of",
    "daughter of",
    "s/o",
    "d/o",
    "w/o",
    "c/o",
    "bank",
    "branch",
    "school",
    "college",
    "university",
    "institute",
    "nominee",
    "पिता",
    "माता",
    "पति",
    "संस्थान",
    "बैंक",
    "शाखा",
)
_ID_ANCHOR_KEYWORDS = (
    "father's name",
    "fathers name",
    "father name",
    "पिता का नाम",
    "dob",
    "d.o.b",
    "date of birth",
    "year of birth",
    "yob",
    "जन्म तिथि",
)
_HEADER_IGNORE_PHRASES = (
    "income tax department",
    "govt. of india",
    "govt of india",
    "government of india",
    "permanent account number",
    "unique identification",
    "uidai",
    "aadhaar",
    "aadhar",
    "mera aadhaar",
    "election commission",
    "passbook",
    "marksheet",
    "statement of marks",
    "income certificate",
    "caste certificate",
    "signature",
    "आयकर विभाग",
    "भारत सरकार",
    "स्थायी खाता संख्या",
    "मेरा आधार",
)

_PAN_KEYWORDS = (
    "income tax department",
    "permanent account number",
    "govt. of india",
    "govt of india",
    "pan",
    "आयकर विभाग",
    "स्थायी खाता संख्या",
)
_AADHAAR_KEYWORDS = (
    "uidai",
    "unique identification",
    "unique identification authority",
    "aadhaar",
    "aadhar",
    "mera aadhaar",
    "government of india",
    "आधार",
    "मेरा आधार",
    "भारतीय विशिष्ट पहचान प्राधिकरण",
)
_PASSBOOK_KEYWORDS = (
    "passbook",
    "ifsc",
    "ifs code",
    "account no",
    "account number",
    "bank account",
    "savings account",
    "current account",
    "a/c no",
    "acct no",
    "branch",
    "cif",
    "micr",
    "bank of",
    "state bank",
    "पासबुक",
    "खाता संख्या",
    "शाखा",
    "आईएफएससी",
)
_MARKSHEET_KEYWORDS = (
    "marksheet",
    "mark sheet",
    "statement of marks",
    "board of secondary",
    "higher secondary",
    "university",
    "cgpa",
    "sgpa",
    "roll no",
    "roll number",
    "अंक तालिका",
    "रोल नंबर",
)
_INCOME_CERT_KEYWORDS = (
    "income certificate",
    "annual income",
    "family income",
    "total annual income",
    "आय प्रमाण पत्र",
    "वार्षिक आय",
)
_CASTE_CERT_KEYWORDS = (
    "caste certificate",
    "scheduled caste",
    "scheduled tribe",
    "other backward class",
    "जाति प्रमाण पत्र",
)
_CASTE_BOUNDARY_RE = re.compile(r"\b(?:obc|sc/st)\b", re.IGNORECASE)

_ACCOUNT_LABEL_KEYWORDS = (
    "account no",
    "account number",
    "a/c no",
    "a/c",
    "acct no",
    "acct",
    "sb a/c",
    "खाता संख्या",
    "खाता नं",
    "खाता क्र",
)
_NON_ACCOUNT_KEYWORDS = (
    "mobile",
    "phone",
    "tel",
    "fax",
    "micr",
    "cif",
    "customer id",
    "aadhaar",
    "aadhar",
    "uid",
    "vid",
    "मोबाइल",
    "दूरभाष",
)
_DOB_LABEL_KEYWORDS = ("dob", "d.o.b", "date of birth", "birth", "yob", "जन्म तिथि", "जन्म")
_NON_DOB_DATE_KEYWORDS = (
    "issue",
    "issued",
    "print",
    "printed",
    "download",
    "valid",
    "expiry",
    "statement",
    "opening",
    "जारी",
)


def _create_paddle_ocr(lang: str) -> Any:
    """Instantiate PaddleOCR across 2.x and 3.x APIs with quiet logging and graceful fallback."""
    if not HAS_PADDLE_OCR or PaddleOCR is None:
        return None
    try:
        for logger_name in ("paddleocr", "ppocr", "paddlex"):
            logging.getLogger(logger_name).setLevel(logging.ERROR)

        init_params = inspect.signature(PaddleOCR.__init__).parameters
        if "use_textline_orientation" in init_params:
            # PaddleOCR 3.x maps use_angle_cls -> use_textline_orientation, rejects show_log,
            # and hits a PIR oneDNN bug on PaddlePaddle 3.3 CPU unless enable_mkldnn=False.
            return PaddleOCR(
                lang=lang,
                use_textline_orientation=True,
                enable_mkldnn=False,
            )
        return PaddleOCR(lang=lang, use_angle_cls=True, show_log=False)
    except Exception as exc:
        logging.getLogger("app.documents.paddle_ocr").warning(
            "PaddleOCR initialization failed for lang=%s: %s", lang, exc
        )
        return None


def _polygon_to_bbox(poly: Any) -> Dict[str, int]:
    """Convert a polygon or [x1, y1, x2, y2] box into {x, y, width, height}."""
    arr = np.asarray(poly, dtype=float)
    if arr.ndim == 1 and arr.size == 4:
        x1, y1, x2, y2 = arr.tolist()
        x = max(0, int(round(min(x1, x2))))
        y = max(0, int(round(min(y1, y2))))
        w = max(0, int(round(abs(x2 - x1))))
        h = max(0, int(round(abs(y2 - y1))))
        return {"x": x, "y": y, "width": w, "height": h}

    pts = arr.reshape(-1, 2)
    if pts.size == 0:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    min_x = float(np.min(pts[:, 0]))
    min_y = float(np.min(pts[:, 1]))
    max_x = float(np.max(pts[:, 0]))
    max_y = float(np.max(pts[:, 1]))
    x = max(0, int(round(min_x)))
    y = max(0, int(round(min_y)))
    w = max(0, int(round(max_x - min_x)))
    h = max(0, int(round(max_y - min_y)))
    return {"x": x, "y": y, "width": w, "height": h}


def _detect_script(text: str) -> str:
    """Detect whether text uses Latin, Devanagari, or mixed script."""
    has_hi = bool(_DEVANAGARI_RE.search(text))
    has_en = bool(_LATIN_RE.search(text))
    if has_hi and has_en:
        return "mixed"
    if has_hi:
        return "hi"
    return "en"


def _clamp_confidence(value: float) -> float:
    """Clamp confidence score into [0.0, 1.0]."""
    return round(max(0.0, min(1.0, float(value))), 4)


def _clean_name_candidate(raw: str) -> Optional[str]:
    """Normalize and validate an extracted person name string."""
    cleaned = re.sub(r"^[\s:;\-–]+|[\s:;\-–.,]+$", "", raw or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 3:
        return None
    if any(ch.isdigit() for ch in cleaned):
        return None
    if _PAN_RE.search(cleaned) or _IFSC_RE.search(cleaned):
        return None
    lower = cleaned.lower()
    if any(phrase in lower for phrase in _HEADER_IGNORE_PHRASES):
        return None
    alpha_count = sum(1 for ch in cleaned if ch.isalpha())
    if alpha_count < 2:
        return None
    return cleaned


class BilingualDocumentOCR:
    """Bilingual English/Hindi OCR extractor and Indian KYC field parser."""

    def __init__(self, confidence_threshold: float = 0.80) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0.0 and 1.0.")
        self.confidence_threshold = confidence_threshold
        self._engines: Dict[str, PaddleOCR] = {
            "en": _create_paddle_ocr("en"),
            "hi": _create_paddle_ocr("hi"),
        }

    def extract_lines(self, image_bytes: bytes, lang: str = "en") -> List[Dict[str, Any]]:
        """Decode image bytes in memory and run PaddleOCR for the given language.

        Args:
            image_bytes: Raw PNG/JPEG/WebP image bytes.
            lang: Language key (``"en"`` or ``"hi"``).

        Returns:
            List of normalized OCR line dicts with ``text``, ``confidence``,
            and ``bbox`` (``{x, y, width, height}``).
        """
        lang_key = lang.strip().lower()
        if lang_key not in self._engines:
            raise ValueError(f"Unsupported OCR language '{lang}'. Expected 'en' or 'hi'.")
        if not image_bytes:
            raise ValueError("Image payload is empty.")

        np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Failed to decode image bytes with OpenCV.")

        engine = self._engines.get(lang_key)
        if engine is None:
            logging.getLogger("app.documents.paddle_ocr").warning(
                "PaddleOCR engine not loaded for lang=%s. Returning empty line extractions.", lang_key
            )
            return []

        if hasattr(engine, "predict"):
            raw_results = engine.predict(image)
        else:
            raw_results = engine.ocr(image, cls=True)

        return self._normalize_results(raw_results)

    def _normalize_results(self, raw_results: Any) -> List[Dict[str, Any]]:
        """Normalize PaddleOCR 2.x and 3.x outputs into a uniform line list."""
        if not raw_results:
            return []

        lines: List[Dict[str, Any]] = []
        for item in raw_results:
            if item is None:
                continue

            # PaddleOCR 3.x / PaddleX OCRResult mapping interface.
            if isinstance(item, dict) or (hasattr(item, "keys") and "rec_texts" in item):
                texts = item.get("rec_texts") or []
                scores = item.get("rec_scores") or []
                polys = item.get("rec_polys")
                if polys is None:
                    polys = item.get("dt_polys")
                if polys is None:
                    polys = item.get("rec_boxes") or []

                for idx, raw_text in enumerate(texts):
                    text = str(raw_text).strip()
                    if not text:
                        continue
                    score = float(scores[idx]) if idx < len(scores) else 0.0
                    poly = polys[idx] if idx < len(polys) else [0, 0, 0, 0]
                    lines.append(
                        {
                            "text": text,
                            "confidence": _clamp_confidence(score),
                            "bbox": _polygon_to_bbox(poly),
                        }
                    )
                continue

            # PaddleOCR 2.x list-of-lines format: [[poly, (text, conf)], ...].
            if isinstance(item, list):
                for entry in item:
                    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                        continue
                    poly, rec = entry[0], entry[1]
                    if not isinstance(rec, (list, tuple)) or len(rec) < 2:
                        continue
                    text = str(rec[0]).strip()
                    if not text:
                        continue
                    lines.append(
                        {
                            "text": text,
                            "confidence": _clamp_confidence(float(rec[1])),
                            "bbox": _polygon_to_bbox(poly),
                        }
                    )

        return lines

    def classify_and_parse(
        self,
        lines: List[Dict[str, Any]],
        expected_doc_type: Optional[str] = None,
        image_dimensions: Optional[Tuple[int, int]] = None,
    ) -> Tuple[str, float, List[Dict[str, Any]]]:
        """Classify document type and parse structured Indian document fields.

        Args:
            lines: Normalized OCR line dicts produced by ``extract_lines``.
            expected_doc_type: Optional expected document hint (used when zero OCR
                lines are detected for clean ``passport_photo`` or ``signature``).
            image_dimensions: Optional ``(width_px, height_px)`` tuple for
                aspect-ratio confirmation of zero-text media uploads.

        Returns:
            Tuple of ``(doc_type, classification_confidence, parsed_fields)``.
        """
        if not lines:
            expected_norm = (expected_doc_type or "").strip().lower()
            if expected_norm in ("passport_photo", "signature"):
                conf = 0.80
                if image_dimensions and image_dimensions[0] > 0 and image_dimensions[1] > 0:
                    aspect = image_dimensions[0] / float(image_dimensions[1])
                    if expected_norm == "signature":
                        conf = 0.86 if aspect >= 1.4 else 0.72
                    else:
                        conf = 0.86 if 0.65 <= aspect <= 1.15 else 0.72
                return expected_norm, conf, []
            return "unknown", 0.0, []

        full_text = "\n".join(str(line.get("text", "")) for line in lines)
        full_lower = full_text.lower()

        scores: Dict[str, float] = {
            "pan": 0.0,
            "aadhaar": 0.0,
            "bank_passbook": 0.0,
            "marksheet": 0.0,
            "income_certificate": 0.0,
            "caste_certificate": 0.0,
        }

        # Keyword evidence
        pan_kw_hits = sum(1 for kw in _PAN_KEYWORDS if kw in full_lower)
        aadhaar_kw_hits = sum(1 for kw in _AADHAAR_KEYWORDS if kw in full_lower)
        passbook_kw_hits = sum(1 for kw in _PASSBOOK_KEYWORDS if kw in full_lower)
        # Match standalone "account" only outside "permanent account number"
        if "account" in full_lower and re.search(r"\b(?!permanent\s+account)account\b", full_lower):
            passbook_kw_hits += 1
        marksheet_kw_hits = sum(1 for kw in _MARKSHEET_KEYWORDS if kw in full_lower)
        income_kw_hits = sum(1 for kw in _INCOME_CERT_KEYWORDS if kw in full_lower)
        caste_kw_hits = sum(1 for kw in _CASTE_CERT_KEYWORDS if kw in full_lower)
        if _CASTE_BOUNDARY_RE.search(full_text):
            caste_kw_hits += 1

        scores["pan"] += min(0.55, pan_kw_hits * 0.22)
        scores["aadhaar"] += min(0.55, aadhaar_kw_hits * 0.22)
        scores["bank_passbook"] += min(0.60, passbook_kw_hits * 0.18)
        scores["marksheet"] += min(0.85, marksheet_kw_hits * 0.28)
        scores["income_certificate"] += min(0.85, income_kw_hits * 0.35)
        scores["caste_certificate"] += min(0.85, caste_kw_hits * 0.35)

        # Structural regex evidence
        has_pan = bool(_PAN_RE.search(full_text))
        has_ifsc = bool(_IFSC_RE.search(full_text))
        has_strict_aadhaar = any(
            _AADHAAR_STRICT_RE.search(str(line.get("text", "")))
            and "vid" not in str(line.get("text", "")).lower()
            for line in lines
        )
        has_any_uid_with_kw = aadhaar_kw_hits > 0 and any(
            _AADHAAR_RE.search(str(line.get("text", "")))
            and "vid" not in str(line.get("text", "")).lower()
            for line in lines
        )
        has_labeled_account = any(
            any(kw in str(line.get("text", "")).lower() for kw in _ACCOUNT_LABEL_KEYWORDS)
            and self._extract_account_digits(str(line.get("text", ""))) is not None
            for line in lines
        )

        if has_pan:
            scores["pan"] += 0.45
        if has_ifsc:
            scores["bank_passbook"] += 0.45
        if has_labeled_account:
            scores["bank_passbook"] += 0.30
        if has_strict_aadhaar or has_any_uid_with_kw:
            scores["aadhaar"] += 0.45

        best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
        if best_score < 0.30:
            doc_type = "unknown"
            cls_confidence = _clamp_confidence(best_score)
        else:
            doc_type = best_type
            cls_confidence = _clamp_confidence(min(0.99, best_score))

        fields = self._parse_fields(lines, doc_type)
        return doc_type, cls_confidence, fields

    def _make_field(
        self,
        key: str,
        value: str,
        confidence: float,
        bbox: Optional[Dict[str, int]],
        source_text: str,
    ) -> Dict[str, Any]:
        conf = _clamp_confidence(confidence)
        return {
            "key": key,
            "value": value,
            "confidence": conf,
            "needs_user_verification": conf < self.confidence_threshold,
            "bbox": bbox,
            "source_language": _detect_script(source_text),
        }

    @staticmethod
    def _extract_account_digits(text: str) -> Optional[str]:
        for match in _ACCOUNT_CANDIDATE_RE.finditer(text):
            digits = re.sub(r"\D", "", match.group(1))
            if 9 <= len(digits) <= 18:
                return digits
        return None

    def _extract_full_name(
        self, lines: List[Dict[str, Any]], doc_type: str
    ) -> Optional[Dict[str, Any]]:
        """Extract applicant full_name via explicit labels or PAN/ID positional fallback."""
        # Pass 1: Explicit English or Hindi name labels (excluding Father/Mother/etc.).
        for idx, line in enumerate(lines):
            text = str(line.get("text", "")).strip()
            if not text:
                continue
            lower = text.lower()
            conf = float(line.get("confidence", 0.0))
            bbox = line.get("bbox")

            match = _EXPLICIT_NAME_LABEL_RE.match(text)
            if match:
                label_part = match.group("label").lower()
                # Ensure the full prefix before the label does not reference a relation/entity.
                prefix = lower[: match.start("label") + len(label_part)]
                if any(tok in prefix for tok in _RELATION_OR_ENTITY_LABEL_TOKENS):
                    continue
                candidate = _clean_name_candidate(match.group("value"))
                if candidate:
                    return self._make_field("full_name", candidate, conf, bbox, text)

            if _EXPLICIT_NAME_ONLY_RE.match(text) and not any(
                tok in lower for tok in _RELATION_OR_ENTITY_LABEL_TOKENS
            ):
                if idx + 1 < len(lines):
                    next_line = lines[idx + 1]
                    next_text = str(next_line.get("text", "")).strip()
                    candidate = _clean_name_candidate(next_text)
                    if candidate:
                        next_conf = min(conf, float(next_line.get("confidence", 0.0)))
                        return self._make_field(
                            "full_name",
                            candidate,
                            next_conf,
                            next_line.get("bbox"),
                            next_text,
                        )

        # Pass 2: Fallback for PAN / Government ID cards without a Name: label.
        if doc_type in ("pan", "aadhaar", "unknown"):
            for idx, line in enumerate(lines):
                text = str(line.get("text", "")).strip()
                lower = text.lower()
                if not any(anchor in lower for anchor in _ID_ANCHOR_KEYWORDS):
                    continue

                for lookback in (1, 2):
                    cand_idx = idx - lookback
                    if cand_idx < 0:
                        break
                    prev_line = lines[cand_idx]
                    prev_text = str(prev_line.get("text", "")).strip()
                    if not prev_text:
                        continue
                    prev_lower = prev_text.lower()
                    if any(tok in prev_lower for tok in _RELATION_OR_ENTITY_LABEL_TOKENS):
                        continue
                    candidate = _clean_name_candidate(prev_text)
                    if not candidate:
                        continue
                    # Prefer uppercase alphabetic line (standard on PAN cards) or clean alphabetic name on IDs.
                    is_upper_alpha = candidate == candidate.upper() and bool(_LATIN_RE.search(candidate))
                    if is_upper_alpha or doc_type == "aadhaar":
                        return self._make_field(
                            "full_name",
                            candidate,
                            float(prev_line.get("confidence", 0.0)),
                            prev_line.get("bbox"),
                            prev_text,
                        )

        return None

    def _parse_fields(
        self, lines: List[Dict[str, Any]], doc_type: str
    ) -> List[Dict[str, Any]]:
        """Extract prioritized structured fields with per-field confidence."""
        best_by_key: Dict[str, Tuple[int, Dict[str, Any]]] = {}

        def consider(priority: int, field: Dict[str, Any]) -> None:
            key = field["key"]
            existing = best_by_key.get(key)
            if existing is None or (priority, field["confidence"]) > (
                existing[0],
                existing[1]["confidence"],
            ):
                best_by_key[key] = (priority, field)

        full_name_field = self._extract_full_name(lines, doc_type)
        if full_name_field is not None:
            consider(2, full_name_field)

        for line in lines:
            text = str(line.get("text", "")).strip()
            if not text:
                continue
            lower = text.lower()
            conf = float(line.get("confidence", 0.0))
            bbox = line.get("bbox")

            # 1. PAN number
            if doc_type in ("pan", "unknown"):
                pan_match = _PAN_RE.search(text)
                if pan_match:
                    priority = 2 if ("pan" in lower or "permanent" in lower or doc_type == "pan") else 1
                    consider(
                        priority,
                        self._make_field("pan_number", pan_match.group(1).upper(), conf, bbox, text),
                    )

            # 2. IFSC code
            if doc_type in ("bank_passbook", "unknown"):
                ifsc_match = _IFSC_RE.search(text)
                if ifsc_match:
                    priority = 2 if "ifsc" in lower or doc_type == "bank_passbook" else 1
                    consider(
                        priority,
                        self._make_field("ifsc_code", ifsc_match.group(1).upper(), conf, bbox, text),
                    )

            # 3. Bank account number
            if doc_type == "bank_passbook":
                has_acc_label = any(kw in lower for kw in _ACCOUNT_LABEL_KEYWORDS)
                has_non_acc = any(kw in lower for kw in _NON_ACCOUNT_KEYWORDS)
                if not has_non_acc:
                    acc_digits = self._extract_account_digits(text)
                    if acc_digits is not None and (has_acc_label or len(acc_digits) >= 11):
                        priority = 2 if has_acc_label else 1
                        consider(
                            priority,
                            self._make_field("account_number", acc_digits, conf, bbox, text),
                        )

            # 4. Aadhaar 12-digit UID
            if doc_type in ("aadhaar", "unknown"):
                has_non_uid = any(
                    kw in lower for kw in ("vid", "mobile", "phone", "tel", "मोबाइल")
                )
                if not has_non_uid:
                    uid_match = _AADHAAR_STRICT_RE.search(text)
                    if uid_match is None and doc_type == "aadhaar":
                        uid_match = _AADHAAR_RE.search(text)
                    if uid_match:
                        uid_digits = re.sub(r"\D", "", uid_match.group(1))
                        if len(uid_digits) == 12:
                            priority = 2 if doc_type == "aadhaar" else 1
                            consider(
                                priority,
                                self._make_field("id_number", uid_digits, conf, bbox, text),
                            )

            # 5. Date of Birth (DOB / YOB)
            has_dob_label = any(kw in lower for kw in _DOB_LABEL_KEYWORDS)
            has_non_dob = any(kw in lower for kw in _NON_DOB_DATE_KEYWORDS)
            if not has_non_dob:
                dob_match = _DOB_FULL_RE.search(text)
                if dob_match:
                    priority = 2 if has_dob_label else 1
                    consider(
                        priority,
                        self._make_field("dob", dob_match.group(1), conf, bbox, text),
                    )
                elif has_dob_label and doc_type == "aadhaar":
                    yob_match = _DOB_YOB_RE.search(text)
                    if yob_match:
                        consider(
                            1,
                            self._make_field("dob", yob_match.group(1), conf, bbox, text),
                        )

            # 6. Marksheet roll number
            if doc_type == "marksheet":
                roll_match = _ROLL_NO_RE.search(text)
                if roll_match:
                    consider(
                        2,
                        self._make_field("roll_number", roll_match.group(1).upper(), conf, bbox, text),
                    )

            # 7. Income certificate annual income
            if doc_type == "income_certificate":
                if any(kw in lower for kw in ("income", "rs", "inr", "₹", "आय")):
                    inc_match = _ANNUAL_INCOME_RE.search(text)
                    if inc_match:
                        cleaned_income = re.sub(r",", "", inc_match.group(1))
                        consider(
                            2 if ("income" in lower or "आय" in text) else 1,
                            self._make_field("annual_income", cleaned_income, conf, bbox, text),
                        )

        ordered_keys = (
            "full_name",
            "pan_number",
            "id_number",
            "ifsc_code",
            "account_number",
            "dob",
            "roll_number",
            "annual_income",
        )
        results: List[Dict[str, Any]] = []
        for key in ordered_keys:
            if key in best_by_key:
                results.append(best_by_key[key][1])
        return results

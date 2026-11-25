"""4-stage document verification pipeline with OpenCV quality and auto-formatting."""

import base64
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import pymupdf
from rapidfuzz import fuzz

from .auto_formatter import auto_format_image_for_portal

_IMAGE_FORMATTABLE_MIMES = ("image/jpeg", "image/png", "image/webp")


def _normalize_mime(mime_type: str) -> str:
    mime = (mime_type or "").strip().lower()
    if mime == "image/jpg":
        return "image/jpeg"
    return mime


def _coerce_constraints(portal_constraints: Optional[Any]) -> Dict[str, Any]:
    if portal_constraints is None:
        return {}
    if hasattr(portal_constraints, "model_dump"):
        return portal_constraints.model_dump()
    if isinstance(portal_constraints, dict):
        return portal_constraints
    return {}


def _extract_field_value(extracted_fields: List[Dict[str, Any]], target_key: str) -> Optional[str]:
    for field in extracted_fields:
        if isinstance(field, dict):
            key = field.get("key")
            value = field.get("value")
        else:
            key = getattr(field, "key", None)
            value = getattr(field, "value", None)
        if key == target_key and value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _decode_image_for_quality(file_bytes: bytes, mime_type: str) -> Optional[np.ndarray]:
    """Decode image bytes directly, or rasterize page 1 if input is a PDF."""
    if not file_bytes:
        return None
    if mime_type == "application/pdf":
        try:
            with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
                if len(doc) == 0:
                    return None
                pix = doc[0].get_pixmap(dpi=150, alpha=False)
                arr = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
                return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            return None

    np_buf = np.frombuffer(file_bytes, dtype=np.uint8)
    return cv2.imdecode(np_buf, cv2.IMREAD_COLOR)


def _parse_aspect_ratio(aspect_ratio: Optional[Any]) -> Optional[float]:
    """Parse an aspect ratio string like '35:45', '4:3', or '1.33' into width/height."""
    if not aspect_ratio or not isinstance(aspect_ratio, str):
        return None
    raw = aspect_ratio.strip()
    if not raw:
        return None
    for sep in (":", "/"):
        if sep in raw:
            parts = raw.split(sep, 1)
            try:
                w = float(parts[0].strip())
                h = float(parts[1].strip())
                return (w / h) if (w > 0 and h > 0) else None
            except ValueError:
                return None
    try:
        val = float(raw)
        return val if val > 0 else None
    except ValueError:
        return None


class FourStageDocumentValidator:
    """Runs 4-stage technical, visual, semantic, and consistency validation."""

    def __init__(
        self,
        blur_threshold: float = 80.0,
        min_brightness: float = 45.0,
        max_brightness: float = 245.0,
        name_match_threshold: int = 75,
    ) -> None:
        self.blur_threshold = blur_threshold
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.name_match_threshold = name_match_threshold

    def validate(
        self,
        file_bytes: bytes,
        mime_type: str,
        expected_doc_type: str,
        detected_doc_type: str,
        classification_confidence: float,
        extracted_fields: List[Dict[str, Any]],
        profile_full_name: Optional[str] = None,
        portal_constraints: Optional[Dict[str, Any]] = None,
        source_is_pdf: bool = False,
    ) -> Dict[str, Any]:
        """Validate a document across all 4 stages and auto-format images when needed."""
        norm_mime = _normalize_mime(mime_type)
        effective_is_pdf = bool(source_is_pdf or norm_mime == "application/pdf")
        constraints = _coerce_constraints(portal_constraints)
        decoded_image = _decode_image_for_quality(file_bytes, norm_mime)
        is_image = norm_mime.startswith("image/") and decoded_image is not None

        # Stage 1: Technical Specs
        stage_1, needs_auto_format, target_mime = self._run_stage_1(
            file_bytes=file_bytes,
            norm_mime=norm_mime,
            is_image=is_image,
            decoded_image=decoded_image,
            constraints=constraints,
        )

        # Stage 2: Visual Quality via OpenCV
        stage_2 = self._run_stage_2(
            decoded_image=decoded_image,
            source_is_pdf=effective_is_pdf,
        )

        # Stage 3: Document Classification
        stage_3 = self._run_stage_3(
            expected_doc_type=expected_doc_type,
            detected_doc_type=detected_doc_type,
            classification_confidence=classification_confidence,
        )

        # Stage 4: Cross-Field Consistency
        stage_4 = self._run_stage_4(
            extracted_fields=extracted_fields,
            profile_full_name=profile_full_name,
        )

        overall_passed = (
            stage_1["passed"]
            and stage_2["passed"]
            and stage_3["passed"]
            and stage_4["passed"]
        )

        auto_formatted_applied = False
        auto_formatted_summary: Optional[str] = None
        formatted_file_base64: Optional[str] = None
        formatted_mime_type: Optional[str] = None

        if overall_passed and needs_auto_format and is_image:
            formatted_bytes, final_mime, summary = auto_format_image_for_portal(
                image_bytes=file_bytes,
                target_mime=target_mime,
                max_size_kb=constraints.get("max_size_kb"),
                required_width_px=constraints.get("required_width_px"),
                required_height_px=constraints.get("required_height_px"),
                target_aspect_ratio=constraints.get("aspect_ratio"),
            )
            auto_formatted_applied = True
            auto_formatted_summary = summary
            formatted_file_base64 = base64.b64encode(formatted_bytes).decode("ascii")
            formatted_mime_type = final_mime

            # Post-compression / post-resize sharpness re-check.
            post_img = cv2.imdecode(np.frombuffer(formatted_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if post_img is not None:
                post_gray = cv2.cvtColor(post_img, cv2.COLOR_BGR2GRAY)
                post_sharpness = float(cv2.Laplacian(post_gray, cv2.CV_64F).var())
                stage_2["metrics"]["post_format_sharpness"] = round(post_sharpness, 2)
                min_post_sharpness = self.blur_threshold * 0.85
                if post_sharpness < min_post_sharpness:
                    stage_2["warnings"].append(
                        f"Post-compression image sharpness dropped to {post_sharpness:.1f} "
                        f"(< {min_post_sharpness:.1f}); please verify readability."
                    )

        has_non_autoformat_warnings = any(
            bool(stage["warnings"]) for stage in (stage_2, stage_3, stage_4)
        )
        requires_user_override = (not overall_passed) or has_non_autoformat_warnings

        return {
            "overall_passed": overall_passed,
            "requires_user_override": requires_user_override,
            "stage_1_technical_specs": stage_1,
            "stage_2_visual_quality": stage_2,
            "stage_3_document_classification": stage_3,
            "stage_4_cross_field_consistency": stage_4,
            "auto_formatted_applied": auto_formatted_applied,
            "auto_formatted_summary": auto_formatted_summary,
            "formatted_file_base64": formatted_file_base64,
            "formatted_mime_type": formatted_mime_type,
        }

    def _run_stage_1(
        self,
        file_bytes: bytes,
        norm_mime: str,
        is_image: bool,
        decoded_image: Optional[np.ndarray],
        constraints: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool, str]:
        warnings: List[str] = []
        errors: List[str] = []
        passed = True
        needs_auto_format = False

        size_kb = round(len(file_bytes) / 1024.0, 2)
        width_px: Optional[int] = None
        height_px: Optional[int] = None
        if decoded_image is not None:
            height_px, width_px = int(decoded_image.shape[0]), int(decoded_image.shape[1])

        allowed_mimes = [
            _normalize_mime(m) for m in (constraints.get("allowed_mime_types") or []) if m
        ]
        target_mime = norm_mime if norm_mime in _IMAGE_FORMATTABLE_MIMES else "image/jpeg"

        if allowed_mimes and norm_mime not in allowed_mimes:
            allowed_image_mimes = [m for m in allowed_mimes if m in _IMAGE_FORMATTABLE_MIMES]
            if is_image and allowed_image_mimes:
                needs_auto_format = True
                target_mime = "image/jpeg" if "image/jpeg" in allowed_image_mimes else allowed_image_mimes[0]
                warnings.append(
                    f"MIME type '{norm_mime}' is not in {allowed_mimes}; auto-formatting to '{target_mime}'."
                )
            else:
                passed = False
                errors.append(
                    f"File MIME type '{norm_mime}' is not permitted (allowed: {allowed_mimes})."
                )
        elif is_image and allowed_mimes:
            # Prefer JPEG if already permitted and size compression may be needed.
            if target_mime not in allowed_mimes:
                target_mime = allowed_mimes[0]

        max_size_kb = constraints.get("max_size_kb")
        if max_size_kb is not None and size_kb > float(max_size_kb):
            if is_image:
                needs_auto_format = True
                if allowed_mimes and "image/jpeg" in allowed_mimes:
                    target_mime = "image/jpeg"
                warnings.append(
                    f"File size {size_kb:.1f} KB exceeds max_size_kb={max_size_kb}; auto-formatting will compress."
                )
            else:
                passed = False
                errors.append(f"File size {size_kb:.1f} KB exceeds max_size_kb={max_size_kb}.")

        min_size_kb = constraints.get("min_size_kb")
        if min_size_kb is not None and size_kb < float(min_size_kb):
            warnings.append(f"File size {size_kb:.1f} KB is below recommended min_size_kb={min_size_kb}.")

        req_w = constraints.get("required_width_px")
        req_h = constraints.get("required_height_px")
        if is_image and width_px is not None and height_px is not None:
            dim_mismatch = (req_w is not None and width_px != req_w) or (
                req_h is not None and height_px != req_h
            )
            if dim_mismatch:
                needs_auto_format = True
                warnings.append(
                    f"Image dimensions {width_px}x{height_px} do not match required "
                    f"{req_w or 'auto'}x{req_h or 'auto'}; auto-formatting will resize."
                )

        aspect_ratio_str = constraints.get("aspect_ratio")
        target_ratio = _parse_aspect_ratio(aspect_ratio_str)
        actual_ratio: Optional[float] = None
        if is_image and width_px is not None and height_px is not None and height_px > 0:
            actual_ratio = round(width_px / float(height_px), 4)
            if target_ratio is not None and abs(actual_ratio - target_ratio) / target_ratio > 0.12:
                needs_auto_format = True
                warnings.append(
                    f"Image aspect ratio {actual_ratio:.2f} deviates from required "
                    f"'{aspect_ratio_str}' ({target_ratio:.2f}) by >12%; auto-formatting will center-crop."
                )

        metrics = {
            "mime_type": norm_mime,
            "size_kb": size_kb,
            "width_px": width_px,
            "height_px": height_px,
            "aspect_ratio": aspect_ratio_str,
            "actual_aspect_ratio": actual_ratio,
            "needs_auto_format": needs_auto_format,
        }
        return (
            {
                "passed": passed,
                "warnings": warnings,
                "errors": errors,
                "metrics": metrics,
            },
            needs_auto_format,
            target_mime,
        )

    def _run_stage_2(
        self, decoded_image: Optional[np.ndarray], source_is_pdf: bool = False
    ) -> Dict[str, Any]:
        if decoded_image is None:
            return {
                "passed": False,
                "warnings": [],
                "errors": ["Unable to decode image bytes for visual quality inspection."],
                "metrics": {},
            }

        gray = cv2.cvtColor(decoded_image, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        mean_brightness = float(np.mean(gray))

        warnings: List[str] = []
        errors: List[str] = []
        passed = True

        if sharpness < self.blur_threshold:
            passed = False
            errors.append(
                f"Image is too blurry (sharpness {sharpness:.1f} < {self.blur_threshold:.1f})."
            )

        if mean_brightness < self.min_brightness:
            passed = False
            errors.append(
                f"Image is too dark (mean brightness {mean_brightness:.1f} < {self.min_brightness:.1f})."
            )
        elif not source_is_pdf and mean_brightness > self.max_brightness:
            warnings.append(
                f"Image may have glare or overexposure (mean brightness {mean_brightness:.1f} > {self.max_brightness:.1f})."
            )

        return {
            "passed": passed,
            "warnings": warnings,
            "errors": errors,
            "metrics": {
                "sharpness": round(sharpness, 2),
                "mean_brightness": round(mean_brightness, 2),
                "blur_threshold": self.blur_threshold,
                "min_brightness": self.min_brightness,
                "max_brightness": self.max_brightness,
                "source_is_pdf": source_is_pdf,
            },
        }

    def _run_stage_3(
        self,
        expected_doc_type: str,
        detected_doc_type: str,
        classification_confidence: float,
    ) -> Dict[str, Any]:
        expected = (expected_doc_type or "unknown").strip().lower()
        detected = (detected_doc_type or "unknown").strip().lower()
        warnings: List[str] = []
        errors: List[str] = []
        passed = True

        if detected == "unknown":
            if expected != "unknown":
                warnings.append(
                    f"Could not confidently classify document as '{expected}' (detected 'unknown')."
                )
        elif expected != "unknown" and detected != expected:
            passed = False
            errors.append(
                f"Document type mismatch: expected '{expected}', but detected '{detected}'."
            )

        return {
            "passed": passed,
            "warnings": warnings,
            "errors": errors,
            "metrics": {
                "expected_doc_type": expected,
                "detected_doc_type": detected,
                "classification_confidence": round(float(classification_confidence), 4),
            },
        }

    def _run_stage_4(
        self,
        extracted_fields: List[Dict[str, Any]],
        profile_full_name: Optional[str],
    ) -> Dict[str, Any]:
        warnings: List[str] = []
        errors: List[str] = []
        extracted_name = _extract_field_value(extracted_fields, "full_name")

        if profile_full_name and profile_full_name.strip() and extracted_name:
            similarity = float(
                fuzz.token_set_ratio(profile_full_name.strip().lower(), extracted_name.strip().lower())
            )
            if similarity < self.name_match_threshold:
                warnings.append(
                    f"Extracted full_name ('{extracted_name}') differs from profile name "
                    f"(similarity {similarity:.1f}% < {self.name_match_threshold}%)."
                )
            metrics = {
                "compared": True,
                "name_similarity": round(similarity, 2),
                "name_match_threshold": self.name_match_threshold,
            }
        else:
            metrics = {
                "compared": False,
                "name_match_threshold": self.name_match_threshold,
            }

        return {
            "passed": True,
            "warnings": warnings,
            "errors": errors,
            "metrics": metrics,
        }

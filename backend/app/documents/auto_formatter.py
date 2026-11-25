"""In-memory Pillow image auto-formatter for government portal upload specs."""

import io
from typing import Optional, Tuple

from PIL import Image, ImageOps

_MIME_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _normalize_mime(target_mime: str) -> str:
    mime = target_mime.strip().lower()
    if mime == "image/jpg":
        return "image/jpeg"
    if mime not in _MIME_TO_FORMAT:
        raise ValueError(
            f"Unsupported target_mime '{target_mime}'. Supported: image/jpeg, image/png, image/webp."
        )
    return mime


def _prepare_color_mode(image: Image.Image, pillow_format: str) -> Image.Image:
    """Ensure image mode is compatible with the target output format."""
    if pillow_format == "JPEG":
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
        if image.mode in ("RGBA", "LA", "PA"):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        if image.mode != "RGB":
            return image.convert("RGB")
        return image

    if pillow_format == "WEBP" and image.mode == "P":
        return image.convert("RGBA" if "transparency" in image.info else "RGB")

    return image


def _encode_image(image: Image.Image, pillow_format: str, quality: Optional[int] = None) -> bytes:
    """Encode a PIL Image strictly into an in-memory byte buffer."""
    buf = io.BytesIO()
    save_kwargs = {"optimize": True}
    if pillow_format in ("JPEG", "WEBP") and quality is not None:
        save_kwargs["quality"] = quality
    image.save(buf, format=pillow_format, **save_kwargs)
    return buf.getvalue()


def _parse_aspect_ratio(aspect_ratio: Optional[str]) -> Optional[float]:
    """Parse an aspect ratio string like '35:45', '4/3', or '1.33' into width/height."""
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


def _center_crop_to_aspect_ratio(image: Image.Image, target_ratio: float) -> Image.Image:
    """Center-crop image cleanly to match target_ratio without stretching."""
    orig_w, orig_h = image.size
    if orig_w <= 0 or orig_h <= 0 or target_ratio <= 0:
        return image
    current_ratio = orig_w / float(orig_h)
    if abs(current_ratio - target_ratio) / target_ratio <= 0.01:
        return image

    if current_ratio > target_ratio:
        new_w = max(1, int(round(orig_h * target_ratio)))
        left = max(0, (orig_w - new_w) // 2)
        return image.crop((left, 0, min(orig_w, left + new_w), orig_h))

    new_h = max(1, int(round(orig_w / target_ratio)))
    top = max(0, (orig_h - new_h) // 2)
    return image.crop((0, top, orig_w, min(orig_h, top + new_h)))


def auto_format_image_for_portal(
    image_bytes: bytes,
    target_mime: str = "image/jpeg",
    max_size_kb: Optional[int] = None,
    required_width_px: Optional[int] = None,
    required_height_px: Optional[int] = None,
    target_aspect_ratio: Optional[str] = None,
) -> Tuple[bytes, str, str]:
    """Resize, crop, convert, and compress an image in memory to meet portal constraints.

    Args:
        image_bytes: Source image bytes.
        target_mime: Desired output MIME type (default ``"image/jpeg"``).
        max_size_kb: Optional maximum file size in kilobytes.
        required_width_px: Optional exact target width in pixels.
        required_height_px: Optional exact target height in pixels.
        target_aspect_ratio: Optional target aspect ratio (e.g. ``"35:45"``, ``"1:1"``)
            to center-crop to before resizing.

    Returns:
        Tuple of ``(formatted_bytes, final_mime_type, human_readable_summary)``.
    """
    if not image_bytes:
        raise ValueError("Image payload is empty.")
    if max_size_kb is not None and max_size_kb <= 0:
        raise ValueError("max_size_kb must be positive.")

    normalized_mime = _normalize_mime(target_mime)
    pillow_format = _MIME_TO_FORMAT[normalized_mime]
    final_mime = _FORMAT_TO_MIME[pillow_format]
    parsed_ratio = _parse_aspect_ratio(target_aspect_ratio)

    with Image.open(io.BytesIO(image_bytes)) as opened:
        orig_width, orig_height = opened.size
        orig_format = opened.format or "UNKNOWN"
        working = ImageOps.exif_transpose(opened)
        working = _prepare_color_mode(working, pillow_format)

        if parsed_ratio is not None:
            working = _center_crop_to_aspect_ratio(working, parsed_ratio)

        # Resize to exact dimensions if both are given, or preserve aspect ratio if one is given.
        if required_width_px is not None and required_height_px is not None:
            if (working.width, working.height) != (required_width_px, required_height_px):
                working = working.resize(
                    (required_width_px, required_height_px),
                    Image.Resampling.LANCZOS,
                )
        elif required_width_px is not None and working.width != required_width_px:
            scaled_h = max(1, int(round(working.height * (required_width_px / working.width))))
            working = working.resize((required_width_px, scaled_h), Image.Resampling.LANCZOS)
        elif required_height_px is not None and working.height != required_height_px:
            scaled_w = max(1, int(round(working.width * (required_height_px / working.height))))
            working = working.resize((scaled_w, required_height_px), Image.Resampling.LANCZOS)

        applied_quality: Optional[int] = 92 if pillow_format in ("JPEG", "WEBP") else None
        formatted_bytes = _encode_image(working, pillow_format, quality=applied_quality)

        if max_size_kb is not None:
            max_bytes = max_size_kb * 1024
            if len(formatted_bytes) > max_bytes:
                quality_steps = list(range(92, 34, -8))
                if quality_steps[-1] != 35:
                    quality_steps.append(35)

                fitted = False
                for scale_attempt in range(7):
                    if pillow_format in ("JPEG", "WEBP"):
                        for quality in quality_steps:
                            candidate = _encode_image(working, pillow_format, quality=quality)
                            formatted_bytes = candidate
                            applied_quality = quality
                            if len(candidate) <= max_bytes:
                                fitted = True
                                break
                    else:
                        candidate = _encode_image(working, pillow_format)
                        formatted_bytes = candidate
                        if len(candidate) <= max_bytes:
                            fitted = True

                    if fitted:
                        break

                    next_w = max(1, int(round(working.width * 0.85)))
                    next_h = max(1, int(round(working.height * 0.85)))
                    if (next_w, next_h) == (working.width, working.height) or min(next_w, next_h) < 16:
                        break
                    working = working.resize((next_w, next_h), Image.Resampling.LANCZOS)
                    if scale_attempt == 6:
                        formatted_bytes = _encode_image(
                            working,
                            pillow_format,
                            quality=quality_steps[-1] if pillow_format in ("JPEG", "WEBP") else None,
                        )

        final_width, final_height = working.size

    orig_kb = len(image_bytes) / 1024.0
    final_kb = len(formatted_bytes) / 1024.0
    quality_note = f", quality={applied_quality}" if applied_quality is not None else ""
    summary = (
        f"Formatted {orig_format} ({orig_width}x{orig_height}, {orig_kb:.1f} KB) -> "
        f"{final_mime} ({final_width}x{final_height}, {final_kb:.1f} KB{quality_note})."
    )
    return formatted_bytes, final_mime, summary

"""Image handling for document extraction.

Validates the incoming base64 payload and turns it into a data URI the vision
model can consume.
"""

import base64
import binascii

_IMAGE_BASE64_FORMAT = "image_base64"


def _detect_image_mime(decoded: bytes) -> str:
    if decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if decoded.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if decoded.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP":
        return "image/webp"
    # Unrecognized magic bytes: default to PNG rather than failing, so a valid
    # but unsniffable image still reaches the vision model.
    return "image/png"


def build_image_data_uri(content_b64: str, *, max_bytes: int, content_format: str = _IMAGE_BASE64_FORMAT) -> str:
    """Validate base64 image bytes and return an image data URI.

    Raises ``ValueError`` on empty, non-base64, or oversized content. A
    populated-but-invalid image is a bad *value* on an otherwise well-formed
    request, so the caller turns this into a 200 error envelope rather than a
    crash.
    """
    if content_format != _IMAGE_BASE64_FORMAT:
        raise ValueError(f"unsupported content_format: {content_format}")
    if not content_b64:
        raise ValueError("empty image content")
    try:
        decoded = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"content is not valid base64: {exc}") from exc
    if len(decoded) > max_bytes:
        raise ValueError(f"image exceeds max size ({len(decoded)} > {max_bytes} bytes)")
    mime_type = _detect_image_mime(decoded)
    return f"data:{mime_type};base64,{content_b64}"

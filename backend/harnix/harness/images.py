"""User-supplied images. They are untrusted input: the real type comes from the bytes (never the client's
claim), only raster formats are accepted (no SVG: it can carry script), and sizes are bounded.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass

ALLOWED_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_SIDE = 16_384  # pixels; larger images are refused instead of being sent to the model
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


class ImageError(ValueError):
    """The image cannot be accepted; the message says why and is safe to show to the user."""


@dataclass(frozen=True)
class ValidImage:
    name: str
    media_type: str
    data: bytes
    sha256: str
    width: int | None
    height: int | None


def sniff(data: bytes) -> str | None:
    """The media type the bytes really are, or None if they are not an accepted raster image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def dimensions(data: bytes, media_type: str) -> tuple[int, int] | None:
    """Pixel size read from the header only (no decoding); None when it cannot be read."""
    try:
        if media_type == "image/png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if media_type == "image/gif":
            return struct.unpack("<HH", data[6:10])
        if media_type == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2 : i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return w, h
                i += 2 + length
        if media_type == "image/webp":
            kind = data[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if kind == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
    except (struct.error, IndexError):
        return None
    return None


def clean_name(name: str, fallback: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[^\w .()\-]", "_", base).strip(" .")[:80]
    return base or fallback


def validate_image(name: str, declared_type: str, b64: str, *, max_bytes: int, index: int = 1) -> ValidImage:
    label = f"Image {index}" + (f" ({clean_name(name, '')})" if clean_name(name, "") else "")
    if len(b64) > (max_bytes * 4) // 3 + 8:
        raise ImageError(f"{label} is larger than {max_bytes // (1024 * 1024)} MB")
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ImageError(
            f"{label} is not valid base64 data (send the file bytes only, without a data: prefix)"
        ) from None
    if not data:
        raise ImageError(f"{label} is empty")
    if len(data) > max_bytes:
        raise ImageError(f"{label} is larger than {max_bytes // (1024 * 1024)} MB")
    real = sniff(data)
    if real is None:
        raise ImageError(
            f"{label} is not a PNG, JPEG, GIF or WebP image (SVG and other formats are not accepted)"
        )
    if declared_type and declared_type != real:
        raise ImageError(f"{label} says it is {declared_type} but its bytes are {real}")
    dims = dimensions(data, real)
    if dims and (dims[0] > MAX_SIDE or dims[1] > MAX_SIDE or dims[0] < 1 or dims[1] < 1):
        raise ImageError(f"{label} is {dims[0]}x{dims[1]} pixels; the limit is {MAX_SIDE} on each side")
    return ValidImage(
        clean_name(name, f"image-{index}.{EXTENSIONS[real]}"),
        real,
        data,
        hashlib.sha256(data).hexdigest(),
        dims[0] if dims else None,
        dims[1] if dims else None,
    )


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

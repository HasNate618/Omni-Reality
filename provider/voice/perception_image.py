"""Bounded JPEG framing/dimension validation, not an entropy-stream decoder."""
from __future__ import annotations

MAX_JPEG_BYTES = 65_536
MAX_JPEG_BASE64 = 4 * ((MAX_JPEG_BYTES + 2) // 3)
MAX_IMAGE_SIDE = 640


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read a baseline/progressive 8-bit colour JPEG header without decoding pixels."""
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        return None
    pos = 2
    dimensions = None
    while pos + 4 <= len(data):
        if data[pos] != 255:
            return None
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos + 3 > len(data):
            return None
        marker = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 3], 'big')
        if length < 2 or pos + 1 + length > len(data):
            return None
        body = data[pos + 3:pos + 1 + length]
        if marker in (0xC0, 0xC2):
            if len(body) != 15 or body[0] != 8 or body[5] != 3:
                return None
            dimensions = (int.from_bytes(body[3:5], 'big'), int.from_bytes(body[1:3], 'big'))
        if marker == 0xDA:
            return dimensions if len(body) >= 6 and pos + 1 + length < len(data) - 2 else None
        pos += 1 + length
    return None


def validate_jpeg(jpeg: bytes | None, envelope: dict | None) -> str | None:
    """Return a closed rejection reason, or None. Never return any media content."""
    if not jpeg:
        return 'missing_image'
    if len(jpeg) > MAX_JPEG_BYTES:
        return 'image_over_cap'
    dims = jpeg_dimensions(jpeg)
    if dims is None:
        return 'bad_jpeg'
    if min(dims) < 1 or max(dims) > MAX_IMAGE_SIDE:
        return 'image_dimensions'
    if envelope is None:
        return 'missing_envelope'
    if dims != (envelope.get('sent_w'), envelope.get('sent_h')):
        return 'dimension_mismatch'
    return None

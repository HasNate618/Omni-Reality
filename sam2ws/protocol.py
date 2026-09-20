import base64
import binascii
import io

import numpy as np
from PIL import Image

PROTOCOL_VERSION = 1
MAX_JPEG_BYTES = 350_000

ERROR_CODES = ("invalid", "evicted", "busy", "oom")


class ProtocolError(Exception):
    def __init__(self, code, detail=""):
        assert code in ERROR_CODES, code
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(s: str, limit: int) -> bytes:
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ProtocolError("invalid", f"bad base64: {e}")
    if len(raw) > limit:
        raise ProtocolError("invalid", f"payload {len(raw)} over limit {limit}")
    return raw


def build_request(session_id, frame_id, t_unix_ns, w, h, jpeg_bytes, clicks, remove):
    for c in clicks:
        assert set(c) == {"x", "y", "label", "obj_id"}, c
        assert c["label"] in (0, 1), c
    assert set(remove) == {"obj_ids", "all"}, remove
    return {
        "v": PROTOCOL_VERSION,
        "type": "frame",
        "session_id": session_id,
        "frame_id": frame_id,
        "t_unix_ns": t_unix_ns,
        "image": {"w": w, "h": h, "jpeg_b64": _b64e(jpeg_bytes)},
        "clicks": list(clicks),
        "remove": {"obj_ids": list(remove["obj_ids"]), "all": bool(remove["all"])},
    }


def validate_request(obj):
    if not isinstance(obj, dict) or obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("invalid", "version")
    if obj.get("type") != "frame":
        raise ProtocolError("invalid", "type")
    for key in ("session_id", "frame_id", "t_unix_ns", "image", "clicks", "remove"):
        if key not in obj:
            raise ProtocolError("invalid", f"missing {key}")
    img = obj["image"]
    raw = _b64d(img["jpeg_b64"], MAX_JPEG_BYTES)
    if len(raw) == 0:
        raise ProtocolError("invalid", "empty image")
    obj = dict(obj)
    obj["image"] = dict(img)
    obj["image"]["jpeg_raw"] = raw
    build_request(
        obj["session_id"], obj["frame_id"], obj["t_unix_ns"],
        img["w"], img["h"], b"\xff\xd8", obj["clicks"], obj["remove"],
    )
    return obj


def encode_mask_png(mask) -> bytes:
    arr = (np.asarray(mask) > 0).astype(np.uint8) * 255
    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def build_reply(frame_id, masks):
    return {
        "v": PROTOCOL_VERSION,
        "type": "result",
        "frame_id": frame_id,
        "objects": [
            {
                "obj_id": obj_id,
                "mask_png_b64": _b64e(encode_mask_png(mask)),
                "mask_w": int(np.asarray(mask).shape[1]),
                "mask_h": int(np.asarray(mask).shape[0]),
                "scale_to_source": float(scale),
            }
            for obj_id, mask, scale in masks
        ],
    }


def build_error(frame_id, code, detail=""):
    assert code in ERROR_CODES, code
    return {
        "v": PROTOCOL_VERSION,
        "type": "error",
        "frame_id": frame_id,
        "code": code,
        "detail": detail,
    }

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def new_ulid() -> str:
    ms = int(time.time() * 1000)
    time_chars = []
    for _ in range(10):
        time_chars.append(_CROCKFORD[ms % 32])
        ms //= 32
    rand = int.from_bytes(os.urandom(10), "big")
    rand_chars = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand % 32])
        rand //= 32
    return "".join(reversed(time_chars)) + "".join(reversed(rand_chars))

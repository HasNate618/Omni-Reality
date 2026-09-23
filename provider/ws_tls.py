"""Shared TLS trust for outbound websocket calls to the gateway.

Python.org builds ship with an empty default trust store
(`ssl.get_default_verify_paths().cafile` is None), so the websockets default
context fails every handshake with SSLCertVerificationError. httpx bundles
certifi of its own accord, which is why the HTTP legs work on a machine where
the websocket legs do not. Trust certifi explicitly so both behave the same.
"""
from __future__ import annotations

import ssl


def gateway_ssl_context() -> ssl.SSLContext:
    """A verifying TLS context backed by certifi's CA bundle."""
    import certifi

    return ssl.create_default_context(cafile=certifi.where())

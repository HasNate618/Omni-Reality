"""LAN coordinator package (Task 6, offline slice 2).

Holds per-connection session state for the Quest <-> laptop coordinator.
No model calls, no network calls, no audio: hello/ping handling plus one
hardcoded laptop-authored mark. Never touches yibuapi or any API key.
"""

from __future__ import annotations

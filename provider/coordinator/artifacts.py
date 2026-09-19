"""HTTP serving for generated GLB artifacts (ULID job ids only)."""

from __future__ import annotations

import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from coordinator.jobs import JobStore, coordinator_may_place

logger = logging.getLogger(__name__)

ARTIFACT_PORT = 8766
MAX_GLB_BYTES = 25 * 1024 * 1024
GLTF_MAGIC = b"glTF"

_ULID_RE = re.compile(r"^[0-7][0-9a-hjkmnp-tv-z]{25}$")


def artifact_path(root: Path, job_id: str) -> Path | None:
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        return None
    if not _ULID_RE.fullmatch(job_id):
        return None
    return root / f"{job_id}.glb"


def serve_artifact_bytes(store: JobStore, root: Path, job_id: str) -> bytes | None:
    path = artifact_path(root, job_id)
    if path is None:
        return None
    job = store.jobs.get(job_id)
    if job is None or not coordinator_may_place(store, job_id):
        return None
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size > MAX_GLB_BYTES:
        return None
    data = path.read_bytes()
    if not data.startswith(GLTF_MAGIC):
        return None
    return data


class _ArtifactHandler(BaseHTTPRequestHandler):
    store: JobStore
    root: Path

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.debug(format, *args)

    def do_GET(self) -> None:  # noqa: N802
        if not self.path.startswith("/artifacts/"):
            self.send_error(404)
            return
        name = self.path[len("/artifacts/") :].split("?", 1)[0]
        if not name.endswith(".glb") or ".." in name or "/" in name.strip("/"):
            self.send_error(404)
            return
        job_id = name[:-4]
        data = serve_artifact_bytes(self.store, self.root, job_id)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "model/gltf-binary")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_artifact_server(
    host: str,
    port: int,
    store: JobStore,
    root: Path,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundArtifactHandler",
        (_ArtifactHandler,),
        {"store": store, "root": root},
    )
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="artifact-http")
    thread.start()
    return server

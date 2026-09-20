from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workers.gen_client import BusyError, get_job, queue_job
from workers.sam2_client import inspect_remote


class _Handler(BaseHTTPRequestHandler):
    response_body = b'{"objects":[]}'
    delay_s = 0.0
    last_body = None

    def do_POST(self):
        import time

        time.sleep(type(self).delay_s)
        n = int(self.headers.get("Content-Length", "0"))
        type(self).last_body = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format, *args):
        return


class Sam2ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        _Handler.delay_s = 0.0
        _Handler.response_body = json.dumps(
            {
                "objects": [
                    {
                        "u0": 0.3,
                        "v0": 0.2,
                        "u1": 0.7,
                        "v1": 0.8,
                        "score": 0.9,
                        "mask_png_b64": "QQ==",
                    }
                ],
            }
        ).encode()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_inspect_posts_jpeg_and_target(self) -> None:
        out = inspect_remote(
            base_url=self.base,
            frame_id="01k00000000000000000000001",
            jpeg_b64="qq==",
            target={
                "type": "image_box",
                "frame_id": "01k00000000000000000000001",
                "u0": 0.2,
                "v0": 0.1,
                "u1": 0.8,
                "v1": 0.9,
            },
            phrase="chair",
        )
        self.assertEqual(out["objects"][0]["score"], 0.9)
        posted = json.loads(_Handler.last_body.decode())
        self.assertEqual(posted["jpeg_b64"], "qq==")
        self.assertEqual(posted["phrase"], "chair")

    def test_inspect_timeout(self) -> None:
        _Handler.delay_s = 0.3
        with self.assertRaises(TimeoutError):
            inspect_remote(
                base_url=self.base,
                frame_id="01k00000000000000000000001",
                jpeg_b64="qq==",
                target={
                    "type": "image_point",
                    "frame_id": "01k00000000000000000000001",
                    "u": 0.5,
                    "v": 0.5,
                },
                phrase=None,
                timeout_s=0.05,
            )


class _GenHandler(BaseHTTPRequestHandler):
    queued: dict[str, dict] = {}
    last_post: dict | None = None
    busy = False

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        type(self).last_post = json.loads(self.rfile.read(n).decode())
        if type(self).busy:
            self.send_response(409)
            self.end_headers()
            self.wfile.write(b'{"error":"busy"}')
            return
        job_id = type(self).last_post["job_id"]
        type(self).queued[job_id] = {"status": "queued"}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"queued"}')

    def do_GET(self):
        job_id = self.path.rsplit("/", 1)[-1]
        body = json.dumps(type(self).queued.get(job_id, {"status": "failed"})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class GenClientTests(unittest.TestCase):
    def setUp(self) -> None:
        _GenHandler.queued = {}
        _GenHandler.last_post = None
        _GenHandler.busy = False
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _GenHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_queue_job_posts_job_id(self) -> None:
        out = queue_job(
            base_url=self.base,
            job_id="01k00000000000000000000002",
            frame_id="01k00000000000000000000001",
            jpeg_b64="qq==",
            prompt="chair",
            mask_png_b64=None,
        )
        self.assertEqual(out["status"], "queued")
        assert _GenHandler.last_post is not None
        self.assertEqual(_GenHandler.last_post["job_id"], "01k00000000000000000000002")

    def test_queue_job_busy_is_409(self) -> None:
        _GenHandler.busy = True
        with self.assertRaises(BusyError):
            queue_job(
                base_url=self.base,
                job_id="01k00000000000000000000003",
                frame_id="01k00000000000000000000001",
                jpeg_b64="qq==",
                prompt=None,
                mask_png_b64=None,
            )

    def test_get_job_status(self) -> None:
        _GenHandler.queued["01k00000000000000000000004"] = {"status": "ready"}
        out = get_job(base_url=self.base, job_id="01k00000000000000000000004")
        self.assertEqual(out["status"], "ready")

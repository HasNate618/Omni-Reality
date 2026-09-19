"""Loopback WebSocket server: one TrackingSession per connection.

Test-only helper `serve_in_test` binds an ephemeral port for the suite.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager

import websockets

from sam2ws import protocol

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
MAX_MSG_BYTES = 2 * 1024 * 1024


def _refuse_non_loopback(host, allow_lan):
    if host != "127.0.0.1" and not allow_lan:
        raise ValueError(f"refusing non-loopback bind {host} without allow_lan=True")


def _process(session, msg):
    t0 = time.monotonic()
    try:
        req = protocol.validate_request(msg)
    except protocol.ProtocolError as e:
        return protocol.build_error(msg.get("frame_id", -1)
                                    if isinstance(msg, dict) else -1,
                                    e.code, e.detail)
    try:
        img = req["image"]
        frame_idx = session.ingest(img["jpeg_raw"], img["w"], img["h"])
        rem = req["remove"]
        if rem["all"] or rem["obj_ids"]:
            session.remove(rem["obj_ids"], rem["all"])
        for c in req["clicks"]:
            session.click(frame_idx, c["x"], c["y"], c["label"], c["obj_id"])
        masks = session.masks_for_latest()
        reply = protocol.build_reply(req["frame_id"], masks)
        reply["server_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return reply
    except protocol.ProtocolError as e:
        return protocol.build_error(req["frame_id"], e.code, e.detail)
    except Exception as e:  # noqa: BLE001 - never wedge the socket
        name = type(e).__name__
        if "OutOfMemory" in name:
            try:
                session.reset()
            except Exception:
                pass
            return protocol.build_error(req["frame_id"], "oom", name)
        return protocol.build_error(req["frame_id"], "invalid", f"{name}: {e}")


def _make_handler(session_factory):
    async def handle(ws):
        session = session_factory()
        slot = {"msg": None, "dropped": 0}
        wake = asyncio.Event()

        async def processor():
            while True:
                await wake.wait()
                wake.clear()
                msg = slot["msg"]
                slot["msg"] = None
                if msg is None:
                    continue
                reply = await asyncio.to_thread(_process, session, msg)
                try:
                    await ws.send(json.dumps(reply))
                except Exception:
                    return

        task = asyncio.create_task(processor())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    await ws.send(json.dumps(
                        protocol.build_error(-1, "invalid", "not json")))
                    continue
                if slot["msg"] is not None:
                    slot["dropped"] += 1
                slot["msg"] = msg
                wake.set()
        finally:
            task.cancel()
            try:
                session.reset()
            except Exception:
                pass
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
    return handle


async def _serve(host, port, session_factory):
    return await websockets.serve(
        _make_handler(session_factory), host, port, max_size=MAX_MSG_BYTES)


@asynccontextmanager
async def serve_in_test(session):
    """Test-only: serve one fixed session on an ephemeral loopback port."""
    srv = await _serve("127.0.0.1", 0, lambda: session)
    port = srv.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        srv.close()
        await srv.wait_closed()


def serve(host=DEFAULT_HOST, port=DEFAULT_PORT, session_factory=None,
          allow_lan=False):
    """Blocking production entry point (session_factory builds TrackingSession)."""
    _refuse_non_loopback(host, allow_lan)

    async def main():
        srv = await _serve(host, port, session_factory)
        await srv.serve_forever()

    asyncio.run(main())

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


def _cuda_stats(dropped):
    out = {"v": protocol.PROTOCOL_VERSION, "type": "stats_result",
           "dropped": dropped, "peak_alloc_gb": None,
           "peak_reserved_gb": None}
    try:
        import torch
        if torch.cuda.is_available():
            out["peak_alloc_gb"] = round(
                torch.cuda.max_memory_allocated() / 1e9, 3)
            out["peak_reserved_gb"] = round(
                torch.cuda.max_memory_reserved() / 1e9, 3)
    except Exception:
        pass
    return out


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
                if isinstance(msg, dict) and msg.get("type") == "stats":
                    await ws.send(json.dumps(_cuda_stats(slot["dropped"])))
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


_MODELS = {
    "tiny": ("configs/sam2.1/sam2.1_hiera_t.yaml", "sam2.1_hiera_tiny.pt"),
    "small": ("configs/sam2.1/sam2.1_hiera_s.yaml", "sam2.1_hiera_small.pt"),
    "base+": ("configs/sam2.1/sam2.1_hiera_b+.yaml",
              "sam2.1_hiera_base_plus.pt"),
}


def _real_session_factory(model, work_dir, window):
    from sam2.build_sam import build_sam2_video_predictor
    from sam2ws.session import TrackingSession
    import os
    cfg, ckpt = _MODELS[model]
    ckpt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "checkpoints", ckpt)
    predictor = build_sam2_video_predictor(cfg, ckpt_path, device="cuda")
    os.makedirs(work_dir, exist_ok=True)
    return lambda: TrackingSession(predictor, work_dir, window=window)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="tiny", choices=list(_MODELS))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--work-dir", default="/tmp/sam2ws-live")
    ap.add_argument("--window", type=int, default=16)
    ap.add_argument("--allow-lan", action="store_true")
    args = ap.parse_args()
    serve(port=args.port, allow_lan=args.allow_lan,
          session_factory=_real_session_factory(
              args.model, args.work_dir, args.window))

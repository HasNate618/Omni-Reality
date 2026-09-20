import asyncio
import json
import unittest


class FakeSession:
    def ingest(self, jpeg_bytes, w, h):
        return 0

    def click(self, *a):
        pass

    def remove(self, *a):
        pass

    def masks_for_latest(self):
        import numpy as np
        return [(1, np.zeros((4, 4), dtype=bool), 1.0)]

    def reset(self):
        pass


class ServerTest(unittest.TestCase):
    def test_frame_round_trip_and_oversize_rejected(self):
        from sam2ws import protocol, server

        req = protocol.build_request("s", 0, 0, 8, 8, b"\xff\xd8" + b"0" * 10,
                                     [], {"obj_ids": [], "all": False})

        async def go():
            async with server.serve_in_test(FakeSession()) as ws_port:
                import websockets as ws
                async with ws.connect(f"ws://127.0.0.1:{ws_port}") as sock:
                    await sock.send(json.dumps(req))
                    reply = json.loads(await sock.recv())
                    self.assertEqual(reply["type"], "result")
                    self.assertEqual(reply["frame_id"], 0)
                    await sock.send(json.dumps({"v": 1}))
                    err = json.loads(await sock.recv())
                    self.assertEqual(err["code"], "invalid")
        asyncio.run(go())

    def test_stats_reply_shape(self):
        from sam2ws import server

        async def go():
            async with server.serve_in_test(FakeSession()) as ws_port:
                import websockets as ws
                async with ws.connect(f"ws://127.0.0.1:{ws_port}") as sock:
                    await sock.send(json.dumps({"v": 1, "type": "stats"}))
                    reply = json.loads(await sock.recv())
                    self.assertEqual(reply["type"], "stats_result")
                    self.assertIn("dropped", reply)
                    self.assertIn("peak_alloc_gb", reply)
        asyncio.run(go())

import glob
import os
import unittest


@unittest.skipUnless(os.environ.get("SAM2_GPU_TEST") == "1", "needs GPU + checkpoint")
class HarnessGpuTest(unittest.TestCase):
    def test_click_yields_nonempty_mask(self):
        import asyncio
        import base64
        import io
        import json
        import tempfile

        import numpy as np
        import websockets
        from PIL import Image

        from sam2.build_sam import build_sam2_video_predictor
        from sam2ws import server
        from sam2ws.session import TrackingSession

        async def go():
            predictor = build_sam2_video_predictor(
                "configs/sam2.1/sam2.1_hiera_t.yaml",
                "sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
                device="cuda",
            )
            with tempfile.TemporaryDirectory() as d:
                session = TrackingSession(predictor, d)
                async with server.serve_in_test(session) as port:
                    frames = sorted(glob.glob("sam2ws/tests/data/harness_frames/*.jpg"))
                    self.assertEqual(len(frames), 5)
                    async with websockets.connect(f"ws://127.0.0.1:{port}") as sock:
                        last = None
                        for i, path in enumerate(frames):
                            with open(path, "rb") as f:
                                raw = f.read()
                            clicks = [{"x": 320, "y": 180, "label": 1, "obj_id": 1}] \
                                if i == 0 else []
                            req = {"v": 1, "type": "frame", "session_id": "t",
                                   "frame_id": i, "t_unix_ns": 0,
                                   "image": {"w": 640, "h": 360,
                                             "jpeg_b64": base64.b64encode(raw).decode()},
                                   "clicks": clicks,
                                   "remove": {"obj_ids": [], "all": False}}
                            await sock.send(json.dumps(req))
                            last = json.loads(await sock.recv())
                        self.assertEqual(last["type"], "result")
                        self.assertEqual(len(last["objects"]), 1)
                        obj = last["objects"][0]
                        mask = np.array(Image.open(
                            io.BytesIO(base64.b64decode(obj["mask_png_b64"]))))
                        self.assertEqual(tuple(mask.shape), (360, 640))
                        self.assertGreater(int((mask > 0).sum()), 0)
        asyncio.run(go())

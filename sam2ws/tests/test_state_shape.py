import unittest

# Observed upstream facts (tiny, 2026-09-19, extension-disabled build):
# - hydra resolves "configs/sam2.1/..." from the installed package; no
#   absolute path needed.
# - state["images"] is ONE tensor (N,3,1024,1024) fp32 on cuda, not a dict.
# - source frames are resized square to 1024; original size is kept in
#   state["video_height"]/state["video_width"]. Masks must be rescaled
#   back with those, not with a single 1024/source_width factor.
# - Session design (Task 3) therefore re-inits from a disk window instead of
#   mutating the images tensor.


class StateShapeTest(unittest.TestCase):
    def test_inference_state_keys_and_images_type(self):
        from sam2.build_sam import build_sam2_video_predictor
        predictor = build_sam2_video_predictor(
            "configs/sam2.1/sam2.1_hiera_t.yaml",
            "sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
            device="cuda",
        )
        state = predictor.init_state(video_path="sam2ws/tests/data/two_frames")
        self.assertIn("images", state)
        self.assertIn("num_frames", state)
        self.assertEqual(state["num_frames"], 2)

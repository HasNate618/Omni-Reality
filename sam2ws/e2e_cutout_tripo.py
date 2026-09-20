"""SAM2 cutout -> TripoSR end-to-end (integration measurement, not shipped).

Usage: python /tmp/sam2_tripo_e2e.py
Needs: provider venv + LD env + PYTHONPATH=/tmp/triposr:/tmp/triposhim:/tmp:/home/nate/Projects/omni-3d-gen
"""
import io
import json
import sys
import time

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from omegaconf import OmegaConf
from PIL import Image

sys.path.insert(0, "/home/nate/Projects/omni-3d-gen")
from sam2ws.cutout import cutout_rgba
from sam2ws.segment import Segmenter

from tripo_remap import remap_state_dict

IMG = "/home/nate/Downloads/drone.png"
OUT_GLB = "/tmp/drone_samcut.glb"
OUT_JSON = "/tmp/drone_samcut.json"

t = {}
now = lambda: (torch.cuda.synchronize(), time.time())[1]

# --- SAM2 segment (fresh process section would be ideal; single process,
# unload SAM2 before TripoSR to mimic sequential handoff) ---
s = now()
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor
model = build_sam2("configs/sam2.1/sam2.1_hiera_t.yaml",
                   "/home/nate/Projects/omni-3d-gen/sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
                   device="cuda")
seg = Segmenter(SAM2ImagePredictor(model))
t["sam_load_s"] = round(now() - s, 3)

raw = open(IMG, "rb").read()
with Image.open(io.BytesIO(raw)) as im:
    W, H = im.size
print("source:", W, H)
torch.cuda.reset_peak_memory_stats()
s = now()
# center click; drone fills the frame
out = seg.segment_frame(raw, [{"x": W // 2, "y": H // 2, "label": 1, "obj_id": 1}])
t["sam_mask_s"] = round(now() - s, 3)
t["sam_peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 3)
oid, mask = out[0]
print("mask coverage:", round(float(mask.mean()), 4))
assert 0.02 < mask.mean() < 0.8, "click missed the drone?"

rgba = cutout_rgba(raw, mask, margin=0.05)
print("cutout:", rgba.size)
rgba.save("/tmp/drone_cutout.png")

# --- sequential handoff: drop SAM2 ---
del seg, model, out, mask
import gc
gc.collect()
torch.cuda.empty_cache()

# --- TripoSR on the cutout (same steps as tripo_run, rembg replaced) ---
sys.path.insert(0, "/tmp/triposr")
from tsr.system import TSR
from tsr.utils import resize_foreground

s = now()
cfg = OmegaConf.load(hf_hub_download("stabilityai/TripoSR", "config.yaml"))
OmegaConf.resolve(cfg)
tripo = TSR(cfg)
ckpt = torch.load(hf_hub_download("stabilityai/TripoSR", "model.ckpt"),
                  map_location="cpu", weights_only=True)
tripo.load_state_dict(remap_state_dict(ckpt), strict=True)
tripo.renderer.set_chunk_size(8192)
tripo.to("cuda:0")
t["tripo_load_s"] = round(now() - s, 3)

s = now()
image = resize_foreground(rgba, 0.85)
image = np.array(image).astype(np.float32) / 255.0
image = image[:, :, :3] * image[:, :, 3:4] + (1 - image[:, :, 3:4]) * 0.5
image = Image.fromarray((image * 255.0).astype(np.uint8))
t["tripo_pre_s"] = round(now() - s, 3)

torch.cuda.reset_peak_memory_stats()
s = now()
with torch.no_grad():
    scene_codes = tripo([image], device="cuda:0")
t["tripo_infer_s"] = round(now() - s, 3)

s = now()
meshes = tripo.extract_mesh(scene_codes, True, resolution=256)
t["tripo_mc_s"] = round(now() - s, 3)
t["tripo_peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 3)
t["verts"] = int(meshes[0].vertices.shape[0])
t["faces"] = int(meshes[0].faces.shape[0])

s = now()
meshes[0].export(OUT_GLB)
t["export_s"] = round(now() - s, 3)
t["combined_peak_note"] = "sequential: sam CB then tripo CB (freed between)"
print(json.dumps(t, indent=1))
open(OUT_JSON, "w").write(json.dumps(t, indent=1))

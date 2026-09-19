import asyncio
import contextlib
import os

# Let ops without an Apple-GPU (MPS) kernel fall back to CPU. Must be set
# before torch is imported; has no effect on CUDA machines.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import websockets
import json
import base64
import cv2
import numpy as np
import torch
import time
from collections import OrderedDict
from sam2.build_sam import build_sam2_video_predictor
from sam2.sam2_video_predictor import SAM2VideoPredictor

def init_streaming_state(predictor, video_height, video_width):
    compute_device = predictor.device
    inference_state = {}
    inference_state["images"] = []
    inference_state["num_frames"] = 0
    inference_state["offload_video_to_cpu"] = False
    inference_state["offload_state_to_cpu"] = False
    inference_state["video_height"] = video_height
    inference_state["video_width"] = video_width
    inference_state["device"] = compute_device
    inference_state["storage_device"] = compute_device
    
    # inputs on each frame
    inference_state["point_inputs_per_obj"] = {}
    inference_state["mask_inputs_per_obj"] = {}
    # visual features on a small number of recently visited frames for quick interactions
    inference_state["cached_features"] = {}
    # values that don't change across frames
    inference_state["constants"] = {}
    # mapping between client-side object id and model-side object index
    inference_state["obj_id_to_idx"] = OrderedDict()
    inference_state["obj_idx_to_id"] = OrderedDict()
    inference_state["obj_ids"] = []
    # Slice (view) of each object tracking results
    inference_state["output_dict_per_obj"] = {}
    # A temporary storage to hold new outputs when user interact with a frame
    inference_state["temp_output_dict_per_obj"] = {}
    # Frames that already holds consolidated outputs from click or mask inputs
    inference_state["frames_tracked_per_obj"] = {}
    
    return inference_state

def append_frame(inference_state, frame_bgr, image_size=1024, device="cuda"):
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    img_resized = cv2.resize(img_rgb, (image_size, image_size))
    
    # Global dtype: bfloat16/float16 on CUDA to halve memory, float32 elsewhere
    # (float16 on Apple Silicon unless SAM2_MPS_DTYPE=float32)
    global dtype
    img_tensor = torch.from_numpy(img_resized).permute(2, 0, 1).to(device, dtype=dtype)
    img_tensor /= 255.0
    
    if "img_mean" not in inference_state:
        inference_state["img_mean"] = torch.tensor((0.485, 0.456, 0.406), dtype=dtype, device=device)[:, None, None]
        inference_state["img_std"] = torch.tensor((0.229, 0.224, 0.225), dtype=dtype, device=device)[:, None, None]
        
    img_tensor -= inference_state["img_mean"]
    img_tensor /= inference_state["img_std"]
    
    inference_state["images"].append(img_tensor)
    inference_state["num_frames"] += 1
    
    # FREE MEMORY: SAM 2 only needs the raw image tensor for the current frame.
    # The memory bank caches the features of past frames. We nullify old raw images to prevent OOM.
    current_idx = inference_state["num_frames"] - 1
    if current_idx >= 5:
        inference_state["images"][current_idx - 5] = None
        
    return current_idx

def run_streaming_inference(predictor, inference_state, frame_idx):
    """
    Runs the SAM 2 tracking step for a single newly arrived frame.
    """
    predictor.propagate_in_video_preflight(inference_state)
    
    batch_size = predictor._get_obj_num(inference_state)
    if batch_size == 0:
        return None, None
    
    pred_masks_per_obj = [None] * batch_size
    for obj_idx in range(batch_size):
        obj_output_dict = inference_state["output_dict_per_obj"][obj_idx]
        storage_key = "non_cond_frame_outputs"
        
        current_out, pred_masks = predictor._run_single_frame_inference(
            inference_state=inference_state,
            output_dict=obj_output_dict,
            frame_idx=frame_idx,
            batch_size=1,
            is_init_cond_frame=False,
            point_inputs=None,
            mask_inputs=None,
            reverse=False,
            run_mem_encoder=True,
        )
        obj_output_dict[storage_key][frame_idx] = current_out
        inference_state["frames_tracked_per_obj"][obj_idx][frame_idx] = {"reverse": False}
        pred_masks_per_obj[obj_idx] = pred_masks

    if len(pred_masks_per_obj) > 1:
        all_pred_masks = torch.cat(pred_masks_per_obj, dim=0)
    else:
        all_pred_masks = pred_masks_per_obj[0]
        
    _, video_res_masks = predictor._get_orig_video_res_output(inference_state, all_pred_masks)
    return inference_state["obj_ids"], video_res_masks

# --- GLOBAL MODEL LOAD ---
print("Loading SAM 2 model globally on server startup...")
# CUDA (RTX 4060): half precision under autocast, unchanged from the original.
# Apple Silicon (MPS) and CPU: float32 with no autocast, since CUDA autocast
# is a no-op there and half-precision inputs would mismatch float32 weights.
if torch.cuda.is_available():
    device = "cuda"
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
elif torch.backends.mps.is_available():
    device = "mps"
    # float16 autocast is ~2x faster on Apple Silicon with identical masks in our
    # benchmark; SAM2_MPS_DTYPE=float32 opts out.
    dtype = torch.float32 if os.environ.get("SAM2_MPS_DTYPE") == "float32" else torch.float16
else:
    device = "cpu"
    dtype = torch.float32
print(f"Using device={device} dtype={dtype}", flush=True)


def compute_context():
    if device == "cuda":
        return torch.autocast("cuda", dtype=dtype)
    if device == "mps" and dtype != torch.float32:
        return torch.autocast("mps", dtype=dtype)
    return contextlib.nullcontext()


def free_device_memory():
    if device == "cuda":
        torch.cuda.empty_cache()
    elif device == "mps":
        torch.mps.empty_cache()


predictor = SAM2VideoPredictor.from_pretrained("facebook/sam2-hiera-tiny", device=device)
# Attend to at most the 2 temporally closest clicked frames per object, so
# repeated correction clicks don't grow attention cost without bound.
predictor.max_cond_frames_in_attn = 2
try:
    image_size = predictor.model.image_size
except AttributeError:
    image_size = 1024

# Which past frames each object keeps in its tracking memory, by age (frames
# before the current one). The model looks up memory at ages 1-6 and object
# pointers at ages 1-15, and skips any frame that is missing while keeping the
# right temporal encoding for the rest, so thinning this set is a direct
# speed/quality dial for memory attention (the largest cost per object).
# Clicked (conditioning) frames are never pruned.
MEMORY_POLICIES = {
    "full": frozenset(range(1, 16)),  # everything the model can read
    "sparse": frozenset({1, 3, 5}),   # ~-23% per object, IoU 0.99 vs full on a pan test
    "light": frozenset({1, 2}),
}
MEMORY_POLICY = os.environ.get("SAM2_MEMORY", "sparse")
if MEMORY_POLICY not in MEMORY_POLICIES:
    raise SystemExit(f"SAM2_MEMORY must be one of {sorted(MEMORY_POLICIES)}")
KEEP_AGES = MEMORY_POLICIES[MEMORY_POLICY]
PORT = int(os.environ.get("SAM2_WS_PORT", "8765"))
STATS_EVERY = 50


def prune_memory(inference_state, frame_idx):
    """Keep only KEEP_AGES (and the current frame) of each object's non-clicked memory."""
    for per_obj in (inference_state["output_dict_per_obj"], inference_state["frames_tracked_per_obj"]):
        for obj_dict in per_obj.values():
            store = obj_dict.get("non_cond_frame_outputs", obj_dict)
            for idx in [i for i in store if i != frame_idx and frame_idx - i not in KEEP_AGES]:
                del store[idx]


def process_frame(session, frame, clicks):
    """Run one frame (with optional clicks). Returns (obj_ids, uint8 masks HxW) or None."""
    h, w = frame.shape[:2]
    if session["state"] is None:
        session["state"] = init_streaming_state(predictor, h, w)
    inference_state = session["state"]

    with torch.inference_mode(), compute_context():
        frame_idx = append_frame(inference_state, frame, image_size, device)
        out_obj_ids, out_mask_logits = None, None
        if clicks:
            for click in clicks:
                points = np.array([[click["x"], click["y"]]], dtype=np.float32)
                labels = np.array([1], dtype=np.int32)
                _, out_obj_ids, out_mask_logits = predictor.add_new_points_or_box(
                    inference_state=inference_state,
                    frame_idx=frame_idx,
                    obj_id=click["obj_id"],
                    points=points,
                    labels=labels,
                )
            session["has_object"] = True
        elif session["has_object"]:
            out_obj_ids, out_mask_logits = run_streaming_inference(predictor, inference_state, frame_idx)
            prune_memory(inference_state, frame_idx)

        if out_mask_logits is None:
            return None
        # One device->host transfer for all objects instead of one per object.
        masks = (out_mask_logits[:, 0] > 0.0).to(torch.uint8).cpu().numpy()
    return list(out_obj_ids), masks


def device_memory_mb():
    if device == "cuda":
        return torch.cuda.memory_allocated() / 2**20
    if device == "mps":
        return torch.mps.current_allocated_memory() / 2**20
    return float("nan")


def warm_up():
    """Run a click frame and two tracking frames so the first client frame is fast."""
    started = time.monotonic()
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, size=(480, 640, 3), dtype=np.uint8)
    session = {"state": None, "has_object": False}
    process_frame(session, frame, [{"x": 320, "y": 240, "obj_id": 1}])
    process_frame(session, frame, [])
    process_frame(session, frame, [])
    del session
    free_device_memory()
    print(f"Warm-up done in {time.monotonic() - started:.1f} s", flush=True)


async def handler(websocket):
    print("Client connected!", flush=True)
    session = {"state": None, "has_object": False}
    frame_times = []

    try:
        async for message in websocket:
            data = json.loads(message)
            if data["type"] != "frame":
                continue
            started = time.monotonic()
            jpeg_bytes = base64.b64decode(data["jpeg_b64"])
            frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue

            result = process_frame(session, frame, data.get("clicks", []))
            response = {"type": "result", "objects": []}
            if result is not None:
                obj_ids, masks = result
                for obj_id, mask in zip(obj_ids, masks):
                    _, buffer = cv2.imencode(".png", mask * 255)
                    response["objects"].append(
                        {"obj_id": obj_id, "mask_b64": base64.b64encode(buffer).decode("ascii")}
                    )
            await websocket.send(json.dumps(response))

            frame_times.append(time.monotonic() - started)
            if len(frame_times) == STATS_EVERY:
                print(
                    f"[stats] last {STATS_EVERY} frames: mean {1000 * sum(frame_times) / STATS_EVERY:.0f} ms, "
                    f"objects {len(response['objects'])}, device memory {device_memory_mb():.0f} MB",
                    flush=True,
                )
                frame_times.clear()

    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected.", flush=True)
    except Exception as e:
        print(f"Error handling client: {e}", flush=True)
    finally:
        if session["state"] is not None:
            print("Cleaning up GPU memory for disconnected client...", flush=True)
            session["state"] = None
            free_device_memory()


async def main():
    warm_up()
    print(f"Memory policy: {MEMORY_POLICY} (ages {sorted(KEEP_AGES)})", flush=True)
    print(f"Starting SAM 2 WebSocket Server on ws://0.0.0.0:{PORT}", flush=True)
    async with websockets.serve(handler, "0.0.0.0", PORT, max_size=None):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())

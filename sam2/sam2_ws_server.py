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
    dtype = torch.float32
else:
    device = "cpu"
    dtype = torch.float32
print(f"Using device={device} dtype={dtype}")


def compute_context():
    if device == "cuda":
        return torch.autocast("cuda", dtype=dtype)
    return contextlib.nullcontext()


def free_device_memory():
    if device == "cuda":
        torch.cuda.empty_cache()
    elif device == "mps":
        torch.mps.empty_cache()


predictor = SAM2VideoPredictor.from_pretrained("facebook/sam2-hiera-tiny", device=device)
try:
    image_size = predictor.model.image_size
except AttributeError:
    image_size = 1024

async def handler(websocket):
    print("Client connected!")
    inference_state = None
    has_object = False
    
    try:
        async for message in websocket:
            data = json.loads(message)
            if data["type"] == "frame":
                # Decode JPEG
                jpeg_bytes = base64.b64decode(data["jpeg_b64"])
                np_arr = np.frombuffer(jpeg_bytes, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                
                if frame is None:
                    continue
                    
                h, w = frame.shape[:2]
                if inference_state is None:
                    inference_state = init_streaming_state(predictor, h, w)
                    
                with torch.inference_mode(), compute_context():
                    frame_idx = append_frame(inference_state, frame, image_size, device)
                    
                    clicks = data.get("clicks", [])
                    out_mask_logits = None
                    
                    if clicks:
                        for click in clicks:
                            x, y = click["x"], click["y"]
                            obj_id = click["obj_id"]
                            points = np.array([[x, y]], dtype=np.float32)
                            labels = np.array([1], dtype=np.int32)
                            
                            _, out_obj_ids, out_mask_logits = predictor.add_new_points_or_box(
                                inference_state=inference_state,
                                frame_idx=frame_idx,
                                obj_id=obj_id,
                                points=points,
                                labels=labels,
                            )
                            has_object = True
                    elif has_object:
                        out_obj_ids, out_mask_logits = run_streaming_inference(predictor, inference_state, frame_idx)
                        
                    # Process results
                    response = {"type": "result", "objects": []}
                    if out_mask_logits is not None:
                        for i, out_obj_id in enumerate(out_obj_ids):
                            mask = (out_mask_logits[i, 0] > 0.0).cpu().numpy().astype(np.uint8)
                            
                            # Encode the binary mask as a compressed PNG
                            mask_255 = mask * 255
                            _, buffer = cv2.imencode('.png', mask_255)
                            mask_b64 = base64.b64encode(buffer).decode('utf-8')
                            
                            response["objects"].append({
                                "obj_id": out_obj_id,
                                "mask_b64": mask_b64
                            })
                            
                    await websocket.send(json.dumps(response))
                    
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected.")
    except Exception as e:
        print(f"Error handling client: {e}")
    finally:
        if inference_state is not None:
            print("Cleaning up GPU memory for disconnected client...")
            del inference_state
            free_device_memory()

async def main():
    print("Starting SAM 2 WebSocket Server on ws://0.0.0.0:8765")
    async with websockets.serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())

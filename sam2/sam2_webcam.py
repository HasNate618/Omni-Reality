import cv2
import torch
import numpy as np
from collections import OrderedDict
import time

try:
    from sam2.sam2_video_predictor import SAM2VideoPredictor
except ImportError:
    print("Failed to import sam2. Please ensure you have installed it (e.g. via pip install -e .)")
    exit(1)


def init_streaming_state(predictor, video_height, video_width):
    """
    Initializes a blank SAM 2 inference state without loading a video from disk.
    Mimics the output of predictor.init_state() but for an empty sequence.
    """
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
    """
    Appends an OpenCV BGR frame to the inference state, converting it to the 
    normalized PyTorch tensor expected by SAM 2.
    """
    img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    img_resized = cv2.resize(img_rgb, (image_size, image_size))
    
    # Move to GPU before math for massive speedup
    img_tensor = torch.from_numpy(img_resized).permute(2, 0, 1).float().to(device)
    img_tensor /= 255.0
    
    # Use pre-loaded GPU constants if possible, or create them once
    if "img_mean" not in inference_state:
        inference_state["img_mean"] = torch.tensor((0.485, 0.456, 0.406), dtype=torch.float32, device=device)[:, None, None]
        inference_state["img_std"] = torch.tensor((0.229, 0.224, 0.225), dtype=torch.float32, device=device)[:, None, None]
        
    img_tensor -= inference_state["img_mean"]
    img_tensor /= inference_state["img_std"]
    
    inference_state["images"].append(img_tensor)
    inference_state["num_frames"] += 1
    return inference_state["num_frames"] - 1

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

# Global state for OpenCV mouse callback
click_points = []
def mouse_click(event, x, y, flags, param):
    global click_points
    if event == cv2.EVENT_LBUTTONDOWN:
        click_points.append((x, y))

def main():
    # Detect device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading SAM 2 model from Hugging Face on {device.upper()}...")
    
    try:
        # Load the TINY model for speed. (VOS optimization is disabled because Triton is not supported on Windows)
        predictor = SAM2VideoPredictor.from_pretrained(
            "facebook/sam2-hiera-tiny", 
            device=device
        )
    except Exception as e:
        print(f"Failed to load SAM 2 model: {e}")
        print("Please ensure you have huggingface_hub installed: pip install huggingface_hub")
        return

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    ret, frame = cap.read()
    if not ret:
        print("Error: Could not read from webcam.")
        return
        
    h, w = frame.shape[:2]
    
    inference_state = init_streaming_state(predictor, h, w)
    
    cv2.namedWindow("SAM 2 Webcam")
    cv2.setMouseCallback("SAM 2 Webcam", mouse_click)
    
    tracked_object_id = 1
    has_object = False
    
    print("Webcam started. Click on an object to start tracking.")
    
    try:
        image_size = predictor.model.image_size
    except AttributeError:
        image_size = 1024
        
    while True:
        start_time = time.time()
        ret, frame = cap.read()
        if not ret:
            print("Warning: Camera dropped a frame or disconnected!")
            break
            
        frame_idx = append_frame(inference_state, frame, image_size, device=device)
        
        global click_points
        if click_points:
            x, y = click_points.pop()
            points = np.array([[x, y]], dtype=np.float32)
            labels = np.array([1], dtype=np.int32)
            
            _, out_obj_ids, out_mask_logits = predictor.add_new_points_or_box(
                inference_state=inference_state,
                frame_idx=frame_idx,
                obj_id=tracked_object_id,
                points=points,
                labels=labels,
            )
            has_object = True
            
        elif has_object:
            out_obj_ids, out_mask_logits = run_streaming_inference(predictor, inference_state, frame_idx)
        
        display_frame = frame.copy()
        
        if has_object and 'out_mask_logits' in locals() and out_mask_logits is not None:
            mask = (out_mask_logits[0, 0] > 0.0).cpu().numpy().astype(np.uint8)
            
            colored_mask = np.zeros_like(display_frame)
            colored_mask[:, :, 0] = mask * 255
            display_frame = cv2.addWeighted(display_frame, 1, colored_mask, 0.5, 0)
            
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                largest_contour = max(contours, key=cv2.contourArea)
                x, y, w_box, h_box = cv2.boundingRect(largest_contour)
                cv2.rectangle(display_frame, (x, y), (x + w_box, y + h_box), (0, 255, 0), 2)
                cv2.putText(display_frame, f"Object ID: {tracked_object_id}", (x, y - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                            
            cv2.putText(display_frame, f"Tracked objects: 1", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            
        fps = 1.0 / (time.time() - start_time)
        cv2.putText(display_frame, f"FPS: {fps:.1f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        cv2.imshow("SAM 2 Webcam", display_frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    device = "cuda" if torch.cuda.is_available() else "cpu"
    # Autocast only makes sense for CUDA/bfloat16. Disable if on CPU to prevent errors.
    if device == "cuda":
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            main()
    else:
        with torch.inference_mode():
            main()

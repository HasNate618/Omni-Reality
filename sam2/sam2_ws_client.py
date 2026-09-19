import cv2
import json
import base64
import time
import threading
from websockets.sync.client import connect

# Shared global state
latest_frame = None
latest_boxes = []
click_points = []
next_obj_id = 1
running = True

# Lock to protect shared variables
lock = threading.Lock()

def mouse_click(event, x, y, flags, param):
    global next_obj_id
    if event == cv2.EVENT_LBUTTONDOWN:
        with lock:
            click_points.append((x, y, next_obj_id))
            next_obj_id += 1

def network_worker(uri):
    global latest_frame, latest_boxes, running, click_points
    
    print(f"[Network] Connecting to {uri}...")
    try:
        with connect(uri) as websocket:
            print("[Network] Connected to SAM 2 Server!")
            
            while running:
                # 1. Grab the latest frame and pending clicks safely
                with lock:
                    if latest_frame is None:
                        frame_to_send = None
                    else:
                        frame_to_send = latest_frame.copy()
                        
                    clicks_to_send = []
                    while click_points:
                        x, y, obj_id = click_points.pop(0)
                        clicks_to_send.append({"x": x, "y": y, "obj_id": obj_id})
                
                if frame_to_send is None:
                    time.sleep(0.01)
                    continue
                    
                # 2. Compress frame to JPEG
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                _, buffer = cv2.imencode('.jpg', frame_to_send, encode_param)
                jpeg_b64 = base64.b64encode(buffer).decode('utf-8')
                
                # 3. Construct payload and send
                payload = {
                    "type": "frame",
                    "jpeg_b64": jpeg_b64,
                    "clicks": clicks_to_send
                }
                
                try:
                    websocket.send(json.dumps(payload))
                    # 4. Await response synchronously (blocks this background thread)
                    response_str = websocket.recv()
                    response = json.loads(response_str)
                    
                    if response.get("type") == "result":
                        objects = response.get("objects", [])
                        
                        # 5. Update globally shared latest_boxes
                        with lock:
                            latest_boxes = objects
                            
                    # Cap network updates to ~10 FPS to prevent GPU memory buffer overflow
                    time.sleep(0.1)
                except Exception as e:
                    print(f"[Network] Error communicating with server: {e}")
                    break
                    
    except ConnectionRefusedError:
        print(f"[Network] Could not connect to {uri}. Is the server running?")
    except Exception as e:
        print(f"[Network] Disconnected: {e}")
    finally:
        with lock:
            running = False

def main():
    global latest_frame, latest_boxes, running
    
    # Start the network thread
    uri = "ws://localhost:8765"
    net_thread = threading.Thread(target=network_worker, args=(uri,), daemon=True)
    net_thread.start()
    
    # Initialize Camera
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[Camera] Error: Could not open webcam.")
        with lock:
            running = False
        return

    cv2.namedWindow("SAM 2 Real-Time Client")
    cv2.setMouseCallback("SAM 2 Real-Time Client", mouse_click)
    
    print("[Camera] UI Thread started. Click to add objects!")
    
    prev_time = time.time()
    while running:
        current_time = time.time()
        loop_elapsed = current_time - prev_time
        fps = 1.0 / loop_elapsed if loop_elapsed > 0 else 0
        prev_time = current_time
        
        start_time = time.time() # For sleep calculation
        
        ret, frame = cap.read()
        if not ret:
            print("[Camera] Warning: Camera dropped a frame.")
            break
            
        # Push the raw frame to the background thread
        with lock:
            latest_frame = frame
            # Grab a local copy of boxes to draw
            boxes_to_draw = list(latest_boxes)
            
        display_frame = frame.copy()
        
        import numpy as np
        
        # Draw masks (at local 30 FPS regardless of network)
        # We accumulate all masks onto a single colored overlay
        colored_mask = np.zeros_like(display_frame)
        mask_overlay_alpha = 0.5
        has_masks = False
        
        for obj in boxes_to_draw:
            obj_id = obj["obj_id"]
            mask_b64 = obj.get("mask_b64")
            if mask_b64:
                mask_bytes = base64.b64decode(mask_b64)
                mask_arr = np.frombuffer(mask_bytes, np.uint8)
                mask_img = cv2.imdecode(mask_arr, cv2.IMREAD_GRAYSCALE)
                
                # Resize mask back to original frame size just in case (though it should already match)
                if mask_img.shape[:2] != display_frame.shape[:2]:
                    mask_img = cv2.resize(mask_img, (display_frame.shape[1], display_frame.shape[0]))
                
                # Apply color based on obj_id (simple rotating colors)
                colors = [(0, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255)]
                color = colors[obj_id % len(colors)]
                
                colored_mask[mask_img > 127] = color
                has_masks = True
                
                # Find contour to place the ID text
                contours, _ = cv2.findContours(mask_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    largest_contour = max(contours, key=cv2.contourArea)
                    x, y, w, h = cv2.boundingRect(largest_contour)
                    cv2.putText(display_frame, f"ID: {obj_id}", (x, y - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                                
        if has_masks:
            display_frame = cv2.addWeighted(display_frame, 1, colored_mask, mask_overlay_alpha, 0)
                        
        # Draw UI
        if boxes_to_draw:
            cv2.putText(display_frame, f"Tracked objects: {len(boxes_to_draw)}", (10, 60), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                        
        cv2.putText(display_frame, f"Camera FPS: {fps:.1f}", (10, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        cv2.imshow("SAM 2 Real-Time Client", display_frame)
        if cv2.waitKey(1) & 0xFF == 27:
            with lock:
                running = False
            break

    cap.release()
    cv2.destroyAllWindows()
    print("[Camera] Shutting down...")
    
if __name__ == "__main__":
    main()

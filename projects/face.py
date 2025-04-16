from picamera2 import Picamera2
import numpy as np
import time
import threading
import cv2
from PIL import Image
import customtkinter as ctk
from datetime import datetime, timezone, time as dt_time
from insightface.app import FaceAnalysis
from sklearn.metrics.pairwise import cosine_similarity
import database # Uses the database module to get active embeddings

DOWNSCALE_FACTOR = 0.5
face_recog_stop_event = threading.Event()

try:
    # Initialize InsightFace model (should ideally be initialized once in main.py)
    face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    face_app.prepare(ctx_id=0)
    print("[Face] InsightFace model initialized.")
except Exception as e:
    print(f"[Face ERROR] Failed to initialize InsightFace model: {e}")
    face_app = None

# Global dictionary to hold currently loaded face embeddings
# This is populated by load_active_vectors_from_db
face_db = {}

def load_active_vectors_from_db(mac_address):
    """
    Loads currently active face embeddings from the SQLite database.
    Uses database.get_active_embeddings which performs time/date/day checks.
    Populates the global face_db dictionary.
    """
    global face_db
    face_db.clear() # Clear previous data
    print(f"[DEBUG] load_active_vectors_from_db called for MAC: {mac_address}")

    # Query the database for currently active embeddings for this device
    # This function returns [{'user_id':(bio_id), 'person_name':, 'embedding_data': (NumPy array)}, ...]
    embedding_records = database.get_active_embeddings(mac_address)

    loaded_count = 0
    for record in embedding_records:
        try:
            # Construct a unique key (e.g., combining name and bio_id)
            # Ensure this key format is consistent (used in recognition result)
            key = f"{record['person_name']}_{record['user_id']}" # user_id is bio_id here
            embedding_array = record['embedding_data']

            # Validate the loaded embedding
            if isinstance(embedding_array, np.ndarray) and embedding_array.shape == (512,):
                 face_db[key] = embedding_array
                 loaded_count += 1
            else:
                 print(f"[WARN]   Skipping invalid embedding data for key {key} (type: {type(embedding_array)}, shape: {getattr(embedding_array, 'shape', 'N/A')})")

        except KeyError as e:
            print(f"[ERROR] Missing expected key in database record: {e} - Record: {record}")
        except Exception as e:
            print(f"[ERROR] Error processing embedding record '{record.get('user_id', 'N/A')}': {e}")

    print(f"[DEBUG] Finished loading active vectors from DB. {loaded_count} active embeddings loaded into memory.")
    return loaded_count


def recognize_face(frame, threshold=0.5, downscale_factor=DOWNSCALE_FACTOR):
    """
    Recognize the face by downscaling the frame for faster processing.
    Compares against embeddings currently loaded in the global face_db.
    """
    if not face_app:
        print("[ERROR] FaceAnalysis model not initialized. Cannot recognize face.")
        return "Unknown", 0.0
    if not face_db:
        # print("[DEBUG] No active faces loaded in face_db for recognition.") # Can be noisy
        return "Unknown", 0.0

    # --- Frame Preprocessing ---
    h, w, _ = frame.shape
    if h == 0 or w == 0:
        print("[WARN] Received empty frame for recognition.")
        return "Unknown", 0.0
    new_w, new_h = int(w * downscale_factor), int(h * downscale_factor)
    if new_w <= 0 or new_h <= 0:
        print(f"[WARN] Invalid frame dimensions after downscaling: {new_w}x{new_h}. Skipping recognition.")
        return "Unknown", 0.0
    resized_frame = cv2.resize(frame, (new_w, new_h))

    # --- Face Detection and Embedding Extraction ---
    try:
        faces = face_app.get(resized_frame)
    except Exception as e:
        print(f"[ERROR] Error during face_app.get(): {e}")
        return "Unknown", 0.0
    if not faces:
        return "Unknown", 0.0 # No face detected

    # --- Prepare Detected Embedding ---
    try:
        detected_emb = faces[0].embedding
        if detected_emb.ndim == 1:
            detected_emb_2d = detected_emb.reshape(1, -1) # Reshape for cosine_similarity
        else:
             print(f"[WARN] Detected embedding has unexpected dimensions: {detected_emb.shape}. Skipping.")
             return "Unknown", 0.0
    except AttributeError:
        print("[WARN] Detected face object does not have an 'embedding' attribute.")
        return "Unknown", 0.0
    except Exception as e:
        print(f"[ERROR] Error accessing embedding from detected face: {e}")
        return "Unknown", 0.0

    # --- Compare against Database Embeddings ---
    max_sim = 0.0
    identity = "Unknown"
    for name_key, db_emb in face_db.items(): # name_key is "Name_BioID"
        try:
            # Ensure db_emb is also 1D before reshaping
            if db_emb.ndim == 1:
                db_emb_2d = db_emb.reshape(1, -1)
            else:
                print(f"[WARN] DB embedding for '{name_key}' has unexpected dimensions: {db_emb.shape}. Skipping comparison.")
                continue

            sim = cosine_similarity(detected_emb_2d, db_emb_2d)[0][0]

            if sim > max_sim and sim >= threshold: # Use >= for threshold boundary
                identity = name_key # Return the combined key (e.g., "Name_BioID")
                max_sim = sim
        except Exception as e:
            print(f"[ERROR] Error calculating similarity for {name_key}: {e}")

    return identity, max_sim


def open_face_recognition(on_recognition=None, on_failure_callback=None, parent_label=None):
    """Initiates the face recognition process using the camera."""
    if not face_app:
        print("[FATAL] Cannot start face recognition: FaceAnalysis model failed to initialize.")
        if on_failure_callback:
            if parent_label and parent_label.winfo_exists():
                parent_label.after_idle(on_failure_callback)
            else:
                try: on_failure_callback()
                except Exception: pass
        return

    face_recog_stop_event.clear()
    picam2 = Picamera2()
    try:
        # Configure Camera
        preview_config = picam2.create_preview_configuration(main={"size": (640, 480), "format": "RGB888"})
        picam2.configure(preview_config)
        # Start Camera
        picam2.start()
        print("[DEBUG] Camera started successfully for recognition.")
    except Exception as e:
        print(f"[FATAL] Failed to configure or start camera: {e}")
        try: picam2.close() # Attempt to close if start failed
        except Exception: pass
        if on_failure_callback:
             if parent_label and parent_label.winfo_exists():
                parent_label.after_idle(on_failure_callback)
             else:
                try: on_failure_callback()
                except Exception: pass
        return

    # --- GUI Window / Label Handling (same as before) ---
    created_window = False
    if parent_label is None:
        window = ctk.CTkToplevel()
        window.title("Face Recognition")
        window.geometry("800x600")
        parent_label = ctk.CTkLabel(window, text="")
        parent_label.pack(expand=True, fill="both")
        created_window = True
    else:
        window = None

    # --- Threading and Loop Variables ---
    start_time = time.time()
    latest_frame = [None] # Use list to pass mutable object
    recognition_running = True

    # --- Frame Display Loop (runs in main thread via 'after') ---
    def update_frame_display():
        nonlocal recognition_running
        if not recognition_running or face_recog_stop_event.is_set(): return
        if not parent_label or not parent_label.winfo_exists():
            print("[DEBUG] Parent label destroyed, stopping frame update.")
            stop_recognition_internal()
            return

        try:
            frame = picam2.capture_array()
            if frame is None:
                print("[WARN] Captured empty frame from camera.")
                if parent_label.winfo_exists(): parent_label.after(100, update_frame_display)
                return
            latest_frame[0] = frame.copy() # Update for recognition thread

            # --- Resizing and Display Logic (same as before) ---
            display_width = parent_label.winfo_width()
            display_height = parent_label.winfo_height()
            if display_width <= 1 or display_height <= 1: display_width, display_height = 640, 480
            h, w, _ = frame.shape
            if w > 0 and h > 0:
                frame_aspect = w / h; display_aspect = display_width / display_height
                if frame_aspect > display_aspect: target_w = display_width; target_h = int(target_w / frame_aspect)
                else: target_h = display_height; target_w = int(target_h * frame_aspect)

                if target_w > 0 and target_h > 0:
                    im_pil = Image.fromarray(frame)
                    im_resized = im_pil.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    ctk_img = ctk.CTkImage(light_image=im_resized, dark_image=im_resized, size=(target_w, target_h))
                    if parent_label and parent_label.winfo_exists():
                        parent_label.configure(image=ctk_img, text="")
                        parent_label.image = ctk_img
                    else:
                        stop_recognition_internal(); return
                else: print(f"[WARN] Invalid target resize dimensions: {target_w}x{target_h}")

            # Schedule next update if still running
            if recognition_running and not face_recog_stop_event.is_set() and parent_label.winfo_exists():
                 parent_label.after(33, update_frame_display) # ~30 FPS

        except Exception as e:
            print(f"[ERROR] Error capturing/displaying frame: {e}")
            if parent_label and parent_label.winfo_exists() and recognition_running and not face_recog_stop_event.is_set():
                parent_label.after(100, update_frame_display) # Retry after delay
            else: stop_recognition_internal()

    # Start the display loop
    update_frame_display()

    # --- Recognition Task (runs in background thread) ---
    def recognition_task():
        nonlocal recognition_running
        last_recognition_time = time.time()
        recognition_interval = 3 # Time between recognition attempts
        
        print(f"[DEBUG] Recognition loop running at {datetime.now(timezone.utc).astimezone()}")
        
        while recognition_running and not face_recog_stop_event.is_set():
            current_time = time.time()

            # Timeout Check
            if current_time - start_time > 20: # 20 second timeout
                print("[DEBUG] Timeout: No face recognized within 20 seconds.")
                if on_failure_callback:
                    if parent_label and parent_label.winfo_exists(): parent_label.after_idle(on_failure_callback)
                    else: 
                        try: 
                            on_failure_callback()
                        except Exception: 
                            pass
                stop_recognition_internal()
                break # Exit loop

            # Perform Recognition
            if current_time - last_recognition_time >= recognition_interval and latest_frame[0] is not None:
                last_recognition_time = current_time
                frame_copy = latest_frame[0].copy() # Process a copy
                name, score = recognize_face(frame_copy) # Call the updated function

                if name != "Unknown":
                    print(f"[DEBUG] Face recognized in thread: {name} (score: {score:.2f})")
                    print(f"[DEBUG] Recognition time: {datetime.now(timezone.utc).astimezone()}")
                    if on_recognition:
                        # Use after_idle to run callback in main GUI thread
                        if parent_label and parent_label.winfo_exists(): parent_label.after_idle(on_recognition, name, score, frame_copy)
                        else: 
                            try: 
                                on_recognition(name, score, frame_copy)
                            except Exception as e: print(f"Error in direct on_recognition call: {e}")
                    stop_recognition_internal() # Stop after successful recognition
                    break # Exit loop

            time.sleep(0.05) # Small sleep to yield CPU

        print("[DEBUG] Recognition thread finished.")

    # --- Internal Stop Function ---
    def stop_recognition_internal():
        nonlocal recognition_running
        if recognition_running:
            recognition_running = False
            face_recog_stop_event.set() # Signal threads/loops to stop
            print("[DEBUG] Stopping face recognition internal...")
            # Schedule cleanup in main thread
            if parent_label and parent_label.winfo_exists(): parent_label.after_idle(cleanup_resources)
            else: cleanup_resources() # Direct call if no label (might be risky)

    # --- Resource Cleanup Function ---
    def cleanup_resources():
        print("[DEBUG] Cleaning up camera resources...")
        try:
            if 'picam2' in locals() and picam2: # Check if picam2 exists and is not None
                 picam2.stop()
                 print("[DEBUG] Camera stopped.")
                 picam2.close()
                 print("[DEBUG] Camera closed.")
        except Exception as e: print(f"[ERROR] Error stopping/closing camera: {e}")

        if created_window and window is not None and window.winfo_exists():
            print("[DEBUG] Destroying temporary recognition window.")
            window.destroy()
        print("[DEBUG] Cleanup finished.")

    # Start the recognition thread
    recognition_thread = threading.Thread(target=recognition_task, daemon=True)
    recognition_thread.start()

# --- External Stop Function ---
def stop_face_recognition():
    """Externally called function to stop the recognition process."""
    print("[DEBUG] External stop request received.")
    face_recog_stop_event.set()
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
import database

DOWNSCALE_FACTOR = 0.5
face_recog_stop_event = threading.Event()

try:
    face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    face_app.prepare(ctx_id=0)
    print("[Face] InsightFace model initialized.")
except Exception as e:
    print(f"[Face ERROR] Failed to initialize InsightFace model: {e}")
    face_app = None

face_db = {}

def load_active_vectors_from_db(mac_address):
    global face_db
    face_db.clear()
    print(f"[DEBUG] load_active_vectors_from_db called for MAC: {mac_address}")
    embedding_records = database.get_active_embeddings(mac_address)
    loaded_count = 0
    for record in embedding_records:
        try:
            key = f"{record['person_name']}_{record['user_id']}"
            embedding_array = record['embedding_data']
            if isinstance(embedding_array, np.ndarray) and embedding_array.shape == (512,):
                face_db[key] = embedding_array
                loaded_count += 1
            else:
                print(f"[WARN] Skipping invalid embedding data for key {key} (type: {type(embedding_array)}, shape: {getattr(embedding_array, 'shape', 'N/A')})")
        except KeyError as e:
            print(f"[ERROR] Missing expected key in database record: {e} - Record: {record}")
        except Exception as e:
            print(f"[ERROR] Error processing embedding record '{record.get('user_id', 'N/A')}': {e}")
    print(f"[DEBUG] Finished loading active vectors from DB. {loaded_count} active embeddings loaded into memory.")
    return loaded_count

def recognize_face(frame, threshold=0.5, downscale_factor=DOWNSCALE_FACTOR):
    if not face_app:
        print("[ERROR] FaceAnalysis model not initialized. Cannot recognize face.")
        return "Unknown", 0.0
    if not face_db:
        return "Unknown", 0.0
    h, w, _ = frame.shape
    if h == 0 or w == 0:
        print("[WARN] Received empty frame for recognition.")
        return "Unknown", 0.0
    new_w, new_h = int(w * downscale_factor), int(h * downscale_factor)
    if new_w <= 0 or new_h <= 0:
        print(f"[WARN] Invalid frame dimensions after downscaling: {new_w}x{new_h}. Skipping recognition.")
        return "Unknown", 0.0
    resized_frame = cv2.resize(frame, (new_w, new_h))
    try:
        faces = face_app.get(resized_frame)
    except Exception as e:
        print(f"[ERROR] Error during face_app.get(): {e}")
        return "Unknown", 0.0
    if not faces:
        return "Unknown", 0.0
    try:
        detected_emb = faces[0].embedding
        if detected_emb.ndim == 1:
            detected_emb_2d = detected_emb.reshape(1, -1)
        else:
            print(f"[WARN] Detected embedding has unexpected dimensions: {detected_emb.shape}. Skipping.")
            return "Unknown", 0.0
    except AttributeError:
        print("[WARN] Detected face object does not have an 'embedding' attribute.")
        return "Unknown", 0.0
    except Exception as e:
        print(f"[ERROR] Error accessing embedding from detected face: {e}")
        return "Unknown", 0.0
    max_sim = 0.0
    identity = "Unknown"
    for name_key, db_emb in face_db.items():
        try:
            if db_emb.ndim == 1:
                db_emb_2d = db_emb.reshape(1, -1)
            else:
                print(f"[WARN] DB embedding for '{name_key}' has unexpected dimensions: {db_emb.shape}. Skipping comparison.")
                continue
            sim = cosine_similarity(detected_emb_2d, db_emb_2d)[0][0]
            if sim > max_sim and sim >= threshold:
                identity = name_key
                max_sim = sim
        except Exception as e:
            print(f"[ERROR] Error calculating similarity for {name_key}: {e}")
    return identity, max_sim

def open_face_recognition(on_recognition=None, on_failure_callback=None, parent_label=None):
    if not face_app:
        print("[FATAL] Cannot start face recognition: FaceAnalysis model failed to initialize.")
        if on_failure_callback:
            try:
                on_failure_callback()
            except Exception:
                pass
        return
    face_recog_stop_event.clear()
    picam2 = Picamera2()
    try:
        preview_config = picam2.create_preview_configuration(main={"size": (400, 300), "format": "RGB888"})
        picam2.configure(preview_config)
        picam2.start()
        print("[DEBUG] Camera configured successfully at time ", datetime.now(timezone.utc).astimezone())
        print("[DEBUG] Camera started successfully for recognition.")
    except Exception as e:
        print(f"[FATAL] Failed to configure or start camera: {e}")
        try:
            picam2.close()
        except Exception:
            pass
        if on_failure_callback:
            try:
                on_failure_callback()
            except Exception:
                pass
        return
    start_time = time.time()
    recognition_running = True
    def recognition_task():
        nonlocal recognition_running
        print(f"[DEBUG] Recognition loop started at {datetime.now(timezone.utc).astimezone()}")
        while recognition_running and not face_recog_stop_event.is_set():
            current_time = time.time()
            if current_time - start_time > 20:
                print("[DEBUG] Timeout: No face recognized within 20 seconds.")
                if on_failure_callback:
                    try:
                        on_failure_callback()
                    except Exception:
                        pass
                stop_recognition_internal()
                break
            try:
                frame = picam2.capture_array()
            except Exception as e:
                print(f"[ERROR] Error capturing frame: {e}")
                frame = None
            if frame is not None:
                name, score = recognize_face(frame)
                if name != "Unknown":
                    print(f"[DEBUG] Face recognized: {name} (score: {score:.2f}) at {datetime.now(timezone.utc).astimezone()}")
                    if on_recognition:
                        try:
                            on_recognition(name, score, frame)
                        except Exception as e:
                            print(f"[ERROR] Error in on_recognition callback: {e}")
                    stop_recognition_internal()
                    break
            time.sleep(0.1)
        print("[DEBUG] Recognition thread finished.")
    def stop_recognition_internal():
        nonlocal recognition_running
        if recognition_running:
            recognition_running = False
            face_recog_stop_event.set()
            cleanup_resources()
    def cleanup_resources():
        print("[DEBUG] Cleaning up camera resources...")
        try:
            picam2.stop()
            print("[DEBUG] Camera stopped.")
            picam2.close()
            print("[DEBUG] Camera closed.")
        except Exception as e:
            print(f"[ERROR] Error stopping/closing camera: {e}")
        print("[DEBUG] Cleanup finished.")
    recognition_thread = threading.Thread(target=recognition_task, daemon=True)
    recognition_thread.start()

def stop_face_recognition():
    print("[DEBUG] External stop request received.")
    face_recog_stop_event.set()

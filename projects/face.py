from picamera2 import Picamera2
import numpy as np
import os
from insightface.app import FaceAnalysis
from sklearn.metrics.pairwise import cosine_similarity
import threading
import cv2

face_recog_stop_event = threading.Event()
face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
face_app.prepare(ctx_id=0)
face_db = {}

def load_vectors():
    folders = ["employee", "guest"]
    for base in folders:
        emb_folder = os.path.join(base, "embeddings")
        if os.path.exists(emb_folder):
            for file in os.listdir(emb_folder):
                if file.endswith(".npy"):
                    key = os.path.splitext(file)[0]
                    if key not in face_db:
                        emb = np.load(os.path.join(emb_folder, file))
                        face_db[key] = emb
                        print(f"Loaded {base}/{key}")

load_vectors()

def load_new_vectors():
    folders = ["employee", "guest"]
    for base in folders:
        emb_folder = os.path.join(base, "embeddings")
        if os.path.exists(emb_folder):
            for file in os.listdir(emb_folder):
                if file.endswith(".npy"):
                    key = os.path.splitext(file)[0]
                    if key not in face_db:
                        emb = np.load(os.path.join(emb_folder, file))
                        face_db[key] = emb
                        print(f"Loaded new embedding from {base}/{key}")

def recognize_face(frame, threshold=0.5):
    faces = face_app.get(frame)
    for face in faces:
        emb = face.embedding
        max_sim = 0.0
        identity = "Unknown"
        for name, db_emb in face_db.items():
            sim = cosine_similarity([emb], [db_emb])[0][0]
            if sim > max_sim and sim > threshold:
                identity = name
                max_sim = sim
        return identity, max_sim
    return "Unknown", 0.0

def open_face_recognition(on_recognition=None):
    face_recog_stop_event.clear()
    picam2 = Picamera2()
    picam2.preview_configuration.main.size = (640, 480)
    picam2.preview_configuration.main.format = "RGB888"
    picam2.configure("preview")
    picam2.start()
    prev_name = ""
    try:
        while not face_recog_stop_event.is_set():
            load_new_vectors()
            frame = picam2.capture_array()
            name, score = recognize_face(frame)
            if name != "Unknown" and name != prev_name:
                print(f"Hello: {name} (score: {score:.2f})")
                prev_name = name
                if on_recognition:
                    on_recognition(name, score, frame)
            elif name == "Unknown" and prev_name != "Unknown":
                print("No face recognized")
                prev_name = "Unknown"
    except KeyboardInterrupt:
        print("Face recognition interrupted.")
    finally:
        picam2.stop()
        picam2.close()
        print("Camera stopped.")

def stop_face_recognition():
    face_recog_stop_event.set()

import cv2
import os
import numpy as np
from insightface.app import FaceAnalysis

app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
app.prepare(ctx_id=0)

def convert_all(image_folder, emb_folder):
    os.makedirs(emb_folder, exist_ok=True)
    for file in os.listdir(image_folder):
        if file.lower().endswith(('.jpg', '.png', '.jpeg')):
            name = os.path.splitext(file)[0]
            img_path = os.path.join(image_folder, file)
            img = cv2.imread(img_path)

            if img is None:
                continue

            faces = app.get(img)
            if faces:
                emb = faces[0].embedding
                out_path = os.path.join(emb_folder, f"{name}.npy")
                np.save(out_path, emb)
                print(f"Saved: {out_path}")
            else:
                print(f"No image: {img_path}")

# === Convert employee ===
convert_all("employee/images", "employee/embeddings")

# === Convert guest ===
convert_all("guest/images", "guest/embeddings")

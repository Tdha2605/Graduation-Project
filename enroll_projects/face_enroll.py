# face_enroll.py (Optimized Preview + Grid Layout)
from picamera2 import Picamera2, Controls
import numpy as np
import time
import threading
import cv2
from PIL import Image
import customtkinter as ctk
from customtkinter import CTkImage
from datetime import datetime
import base64
import io
from tkinter import messagebox

try:
    from insightface.app import FaceAnalysis
    face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    face_app.prepare(ctx_id=0)
    print("[Face Enroll] InsightFace model initialized.")
except Exception as e:
    print(f"[Face Enroll ERROR] Failed to initialize InsightFace model: {e}")
    face_app = None

face_capture_stop_event = threading.Event()
picam2 = None

def stop_face_capture():
    global picam2
    print("[Face Enroll DEBUG] External stop request received.")
    face_capture_stop_event.set()
    if picam2:
        try:
            if picam2.started:
                 picam2.stop()
                 print("[Face Enroll DEBUG] Camera stopped.")
            picam2.close()
            print("[Face Enroll DEBUG] Camera closed.")
            picam2 = None
        except Exception as e:
            print(f"[Face Enroll ERROR] Error stopping/closing camera on stop request: {e}")

def capture_face_for_enrollment(parent, on_success_callback=None, on_cancel_callback=None):
    global picam2
    if not face_app:
        print("[Face Enroll FATAL] Cannot start face capture: FaceAnalysis model failed to initialize.")
        messagebox.showerror("Lỗi Model", "Không thể khởi tạo model nhận dạng khuôn mặt.", parent=parent)
        if on_cancel_callback: on_cancel_callback()
        return

    face_capture_stop_event.clear()

    enroll_frame = ctk.CTkFrame(parent, fg_color="black")
    enroll_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
    enroll_frame.lift()

    enroll_frame.grid_rowconfigure(0, weight=1)
    enroll_frame.grid_rowconfigure(1, weight=0)
    enroll_frame.grid_rowconfigure(2, weight=0)
    enroll_frame.grid_columnconfigure(0, weight=1)

    camera_label = ctk.CTkLabel(enroll_frame, text="Đang khởi tạo Camera...", text_color="white", font=("Segoe UI", 18))
    camera_label.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="nsew")

    info_label = ctk.CTkLabel(enroll_frame, text="Đưa khuôn mặt vào giữa khung và nhấn Chụp",
                              font=("Segoe UI", 14), text_color="yellow")
    info_label.grid(row=1, column=0, padx=10, pady=5, sticky="s")

    button_frame = ctk.CTkFrame(enroll_frame, fg_color="transparent")
    button_frame.grid(row=2, column=0, padx=10, pady=10, sticky="s")

    button_frame.grid_columnconfigure(0, weight=1)
    button_frame.grid_columnconfigure(1, weight=1)
    button_frame.grid_columnconfigure(2, weight=1)

    button_width = 150
    button_height = 50
    button_font = ("Segoe UI", 18, "bold")

    capture_button = ctk.CTkButton(button_frame, text="Chụp", width=button_width, height=button_height,
                                   font=button_font, fg_color="#4CAF50", hover_color="#66BB6A",
                                   command=lambda: capture_action())
    capture_button.grid(row=0, column=1, padx=10, pady=5)
    capture_button.configure(state="disabled")

    cancel_button = ctk.CTkButton(button_frame, text="Hủy", width=button_width, height=button_height,
                                  font=button_font, fg_color="#f44336", hover_color="#e57373",
                                  command=lambda: cancel_action())
    cancel_button.grid(row=0, column=2, padx=10, pady=5)


    capture_requested = threading.Event()
    current_frame_for_capture = None
    frame_count = 0
    ui_update_interval = 2

    def cancel_action():
        stop_face_capture()
        if enroll_frame.winfo_exists(): enroll_frame.destroy()
        if on_cancel_callback: on_cancel_callback()

    def capture_action():
        capture_requested.set()

    def camera_thread_func():
        global picam2, current_frame_for_capture
        nonlocal frame_count

        try:
            picam2 = Picamera2()
            preview_config = picam2.create_preview_configuration(main={"size": (800, 600), "format": "RGB888"})
            picam2.configure(preview_config)
            picam2.start()
            print("[Face Enroll DEBUG] Camera started for enrollment.")
            if camera_label.winfo_exists():
                 camera_label.after(0, lambda: capture_button.configure(state="normal"))
        except Exception as e:
            print(f"[Face Enroll FATAL] Failed to configure or start camera: {e}")
            if camera_label.winfo_exists():
                 camera_label.configure(text=f"Lỗi Camera: {e}", text_color="red")
            if capture_button.winfo_exists():
                 capture_button.configure(state="disabled")
            if enroll_frame.winfo_exists():
                 enroll_frame.after(3000, cancel_action)
            return

        capture_in_progress = False

        while not face_capture_stop_event.is_set() and not capture_in_progress:
            try:
                frame = picam2.capture_array()
                if frame is None:
                     print("[Face Enroll WARN] Captured empty frame, skipping.")
                     time.sleep(0.1)
                     continue
                current_frame_for_capture = frame.copy()
                frame_count += 1

                if frame_count % ui_update_interval == 0:
                    img_pil = Image.fromarray(frame)
                    target_size = (480, 360)
                    img_pil_resized = img_pil.resize(target_size, Image.Resampling.NEAREST)
                    ctk_image_obj = CTkImage(light_image=img_pil_resized,
                                             dark_image=img_pil_resized,
                                             size=target_size)

                    if camera_label.winfo_exists():
                        camera_label.after(0, lambda img=ctk_image_obj: camera_label.configure(image=img, text=""))


                if capture_requested.is_set():
                    capture_in_progress = True
                    capture_requested.clear()
                    print("[Face Enroll DEBUG] Capture requested.")
                    if info_label.winfo_exists(): info_label.configure(text="Đang xử lý ảnh...", text_color="cyan")
                    if capture_button.winfo_exists(): capture_button.configure(state="disabled")
                    if cancel_button.winfo_exists(): cancel_button.configure(state="disabled")

                    if current_frame_for_capture is not None:
                        try:
                            faces = face_app.get(current_frame_for_capture)
                            if faces and len(faces) == 1:
                                face = faces[0]
                                embedding = face.embedding.astype(np.float32)
                                embedding_bytes = embedding.tobytes()
                                template_b64 = base64.b64encode(embedding_bytes).decode('utf-8')
                                is_success, buffer = cv2.imencode(".jpg", current_frame_for_capture)
                                if is_success:
                                    image_bytes = io.BytesIO(buffer).getvalue()
                                    image_b64 = base64.b64encode(image_bytes).decode('utf-8')
                                    print("[Face Enroll DEBUG] Embedding and Image extracted and encoded.")
                                    if on_success_callback:
                                        stop_face_capture()
                                        if enroll_frame.winfo_exists(): enroll_frame.destroy()
                                        on_success_callback(image_b64, template_b64)
                                    return
                                else:
                                    print("[Face Enroll ERROR] Failed to encode captured frame to JPEG.")
                                    if info_label.winfo_exists(): info_label.configure(text="Lỗi xử lý ảnh. Thử lại.", text_color="red")
                            elif not faces:
                                print("[Face Enroll WARN] No face found in captured frame.")
                                if info_label.winfo_exists(): info_label.configure(text="Không tìm thấy khuôn mặt. Thử lại.", text_color="orange")
                            else:
                                print("[Face Enroll WARN] Multiple faces found in captured frame.")
                                if info_label.winfo_exists(): info_label.configure(text="Nhiều khuôn mặt. Chỉ một người!", text_color="orange")
                        except Exception as e:
                            print(f"[Face Enroll ERROR] Error during face processing: {e}")
                            if info_label.winfo_exists(): info_label.configure(text=f"Lỗi xử lý: {e}. Thử lại.", text_color="red")
                    else:
                        print("[Face Enroll ERROR] Frame for capture was None.")
                        if info_label.winfo_exists(): info_label.configure(text="Lỗi chụp ảnh. Thử lại.", text_color="red")

                    capture_in_progress = False
                    if capture_button.winfo_exists(): capture_button.configure(state="normal")
                    if cancel_button.winfo_exists(): cancel_button.configure(state="normal")
                    if info_label.winfo_exists(): info_label.configure(text="Đưa khuôn mặt vào giữa khung và nhấn Chụp", text_color="yellow")

            except Exception as e:
                print(f"[Face Enroll ERROR] Error in camera loop: {e}")
                if "Camera is not running" in str(e):
                     if info_label.winfo_exists(): info_label.configure(text="Lỗi Camera. Khởi động lại.", text_color="red")
                     stop_face_capture()
                     if enroll_frame.winfo_exists():
                           enroll_frame.after(2000, cancel_action)
                     return
                time.sleep(0.5)
            time.sleep(0.05)

        print("[Face Enroll DEBUG] Camera thread finished.")
        if picam2 and picam2.started:
             try:
                  picam2.stop()
                  print("[Face Enroll DEBUG] Camera stopped in thread exit.")
                  picam2.close()
                  picam2 = None
             except Exception as e:
                  print(f"[Face Enroll ERROR] Error stopping camera in thread exit: {e}")
        if enroll_frame.winfo_exists():
            enroll_frame.destroy()

    camera_thread = threading.Thread(target=camera_thread_func, daemon=True)
    camera_thread.start()
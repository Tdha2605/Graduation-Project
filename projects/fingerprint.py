import customtkinter as ctk
import threading
import time
from pyfingerprint.pyfingerprint import PyFingerprint
from PIL import Image


def show_hardware_error_image():
    """
    Opens a new window displaying ONLY an error image (e.g. hardware_error.png).
    No text, no buttons—just the image.
    """
    error_window = ctk.CTkToplevel()
    error_window.title("Hardware Error")
    error_window.geometry("600x400")
    error_window.resizable(False, False)

    try:
        err_img = Image.open("hardware_error.png")
    except Exception:
        err_img = Image.new("RGB", (600, 400), color="red")
    ctk_img = ctk.CTkImage(light_image=err_img, dark_image=err_img, size=(600, 400))
    
    lbl = ctk.CTkLabel(error_window, image=ctk_img, text="")
    lbl.image = ctk_img
    lbl.pack(expand=True, fill="both")


def show_fingerprint_success_screen(on_close=None):
    """
    Shows a success window with a green check icon and text: “Đã nhận diện vân tay!”.
    After 2 seconds, it closes and calls `on_close()` if provided.
    """
    win = ctk.CTkToplevel()
    win.title("Fingerprint Success")
    win.geometry("400x300")
    win.resizable(False, False)

    # Load a green check icon or entire success image
    try:
        success_img = Image.open("fingerprint_success.png")  # or "green_check.png"
    except Exception:
        # Fallback if not found
        success_img = Image.new("RGB", (128, 128), color="green")
    ctk_success = ctk.CTkImage(light_image=success_img, dark_image=success_img, size=(128, 128))

    icon_label = ctk.CTkLabel(win, image=ctk_success, text="")
    icon_label.image = ctk_success
    icon_label.pack(pady=(40, 10))

    text_label = ctk.CTkLabel(
        win,
        text="Đã nhận diện vân tay!",
        font=ctk.CTkFont(size=18, weight="bold"),
        text_color="green"
    )
    text_label.pack(pady=(0, 20))

    def auto_close():
        win.destroy()
        if on_close:
            on_close()

    # Close after 2 seconds, then call on_close
    win.after(2000, auto_close)


#########################
#  Main Fingerprint Flow
#########################

def open_fingerprint_prompt(on_success_callback=None):
    """
    1) Window #1: Prompt user to place finger (gray icon).
       If hardware fails, show error image.
       If user places finger, move to scanning window.
    """
    prompt_window = ctk.CTkToplevel()
    prompt_window.title("Fingerprint Prompt")
    prompt_window.geometry("400x300")
    prompt_window.resizable(False, False)

    # Optional background color
    prompt_window.configure(fg_color="#1C1C1C")

    # Load gray icon
    try:
        img_grey = Image.open("fingerprint_icon_grey.png")
    except Exception:
        img_grey = Image.new("RGB", (128, 128), color="gray")
    ctk_img_grey = ctk.CTkImage(light_image=img_grey, dark_image=img_grey, size=(128, 128))

    icon_label = ctk.CTkLabel(prompt_window, image=ctk_img_grey, text="")
    icon_label.image = ctk_img_grey
    icon_label.pack(pady=(40, 10))

    text_label = ctk.CTkLabel(
        prompt_window,
        text="Place your finger on the scanner",
        font=ctk.CTkFont(size=16, weight="bold"),
        text_color="white"
    )
    text_label.pack(pady=(0, 20))

    cancel_flag = {"cancel": False}
    def on_cancel():
        cancel_flag["cancel"] = True
        prompt_window.destroy()
    cancel_button = ctk.CTkButton(prompt_window, text="Cancel", command=on_cancel)
    cancel_button.pack(pady=10)

    def wait_for_finger():
        # Try to init sensor
        try:
            f = PyFingerprint('/dev/serial0', 57600, 0xFFFFFFFF, 0x00000000)
            if not f.verifyPassword():
                prompt_window.after(0, prompt_window.destroy)
                show_hardware_error_image()
                return
        except Exception:
            prompt_window.after(0, prompt_window.destroy)
            show_hardware_error_image()
            return

        # Sensor is OK. Wait until user places finger
        while True:
            if cancel_flag["cancel"]:
                return
            try:
                if f.readImage():
                    # Finger detected -> close prompt window
                    prompt_window.after(0, prompt_window.destroy)
                    # Open scanning window
                    open_fingerprint_scanning(f, on_success_callback=on_success_callback)
                    return
            except Exception:
                prompt_window.after(0, prompt_window.destroy)
                show_hardware_error_image()
                return
            time.sleep(0.1)

    threading.Thread(target=wait_for_finger, daemon=True).start()


def open_fingerprint_scanning(sensor, on_success_callback=None):
    """
    2) Window #2: “Mở khóa / Xác thực ID” + blue icon + “Đang quét...”
       If scanning fails => hardware error.
       If success => show success window (#3).
    """
    scan_window = ctk.CTkToplevel()
    scan_window.title("Mở khóa / Xác thực ID")
    scan_window.geometry("400x300")
    scan_window.resizable(False, False)

    try:
        img_blue = Image.open("fingerprint_icon_blue.png")
    except Exception:
        img_blue = Image.new("RGB", (128, 128), color="blue")
    ctk_img_blue = ctk.CTkImage(light_image=img_blue, dark_image=img_blue, size=(128, 128))

    icon_label = ctk.CTkLabel(scan_window, image=ctk_img_blue, text="")
    icon_label.image = ctk_img_blue
    icon_label.pack(pady=(40, 10))

    text_label = ctk.CTkLabel(scan_window, text="Đang quét...", font=ctk.CTkFont(size=16, weight="bold"))
    text_label.pack(pady=(0, 20))

    cancel_flag = {"cancel": False}
    def on_cancel():
        cancel_flag["cancel"] = True
        scan_window.destroy()

    cancel_button = ctk.CTkButton(scan_window, text="Hủy", command=on_cancel)
    cancel_button.pack(pady=10)

    def scanning_process():
        # This is where you'd do a second read, or match the fingerprint in your DB
        start_time = time.time()
        try:
            while True:
                if cancel_flag["cancel"]:
                    return
                # For demo: if we can read again, treat as "matched"
                if sensor.readImage():
                    # Success => close scanning window
                    scan_window.after(0, scan_window.destroy)
                    # Show success window, then on_close => callback
                    scan_window.after(
                        0,
                        lambda: show_fingerprint_success_screen(
                            on_close=on_success_callback
                        )
                    )
                    return
                if time.time() - start_time > 10:
                    # Timeout => hardware error or user not cooperating
                    scan_window.after(0, scan_window.destroy)
                    show_hardware_error_image()
                    return
                time.sleep(0.1)
        except Exception:
            # If sensor read fails => hardware error
            scan_window.after(0, scan_window.destroy)
            show_hardware_error_image()
            return

    threading.Thread(target=scanning_process, daemon=True).start()

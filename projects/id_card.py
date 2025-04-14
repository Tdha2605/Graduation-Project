# id_card.py
import customtkinter as ctk
from tkinter import messagebox

# Sửa hàm để nhận callback
def open_id_card_recognition(on_close_callback=None):
    """
    Hiển thị thông báo và gọi callback (nếu có) sau khi đóng thông báo.
    """
    messagebox.showinfo("Thông Báo", "Chức năng Nhận dạng thẻ CCCD hiện chưa được triển khai.")

    # Gọi hàm callback sau khi messagebox được đóng
    if on_close_callback:
        print("[DEBUG] ID Card message closed, calling callback to return to main menu.")
        on_close_callback()
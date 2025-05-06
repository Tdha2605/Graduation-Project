# Access Control & Monitoring System

This project implements an **access control device** based on a Raspberry Pi. The device can perform **face recognition** and **fingerprint scanning**. It communicates with a server via **MQTT**, sending status updates, receiving commands, and integrating with a door lock system. The device uses a local SQLite database for storing biometric data and configurations, and the server (external to this Pi device code) would typically store data and provide a web client for monitoring.

---

## Table of Contents

1.  [Introduction](#introduction)
2.  [Features](#features)
3.  [Hardware Overview](#hardware-overview)
4.  [Software Overview](#software-overview)
5.  [System Architecture](#system-architecture)
6.  [Flow Diagram](#flow-diagram)
7.  [Installation & Setup](#installation--setup)
8.  [Usage](#usage)
9.  [License](#license)

---

## Introduction

In many facilities, controlling and monitoring access is critical. This project addresses that need by providing:

-   A **touch-based interface** (using `customtkinter`) on an LCD for user interaction and initial configuration.
-   A **camera module** for face recognition.
-   A **fingerprint sensor** for biometric authentication.
-   An **SOS button** and a **manual door open button** for specific scenarios.
-   A **door lock sensor**, a **door lock relay**, and a **buzzer** connected to GPIO for real-time door control and alerts.
-   Communication via **MQTT** with a backend server for centralized logging, biometric data synchronization, and management.

The end goal is a self-contained, Internet-connected device that manages physical access using multiple biometric methods, with robust server communication and local data persistence.

---

## Features

1.  **Face Recognition**: Uses the Raspberry Pi Camera Module (via `Picamera2` and `InsightFace`) to authenticate users.
2.  **Fingerprint Scanning**: Utilizes a serial fingerprint sensor (via `pyfingerprint`) for user authentication. Manages fingerprint templates and positions on the sensor.
3.  **ID Card Option**: UI placeholder for ID card scanning (further implementation needed).
4.  **MQTT Communication**: Publishes and subscribes to topics for:
    -   Device registration (HTTP token retrieval, then MQTT connection).
    -   Health checks.
    -   Biometric data push/synchronization from the server (add, update, delete users/face/fingerprints).
    -   Acknowledgement of received biometric data.
    -   Reporting successful recognitions.
    -   SOS alerts.
    -   Door status updates.
5.  **Door Lock Control & Monitoring**:
    -   Engages or disengages a physical lock via GPIO pins.
    -   Monitors door status (open/closed) using a door sensor.
6.  **Buzzer Alerts**: Activates buzzer for SOS events.
7.  **Buttons**:
    -   **SOS Button**: Triggers an alarm (buzzer) and sends an MQTT alert to the server.
    -   **Manual Open Door Button**: Allows immediate door opening.
8.  **LCD User Interface**: Provides a GUI (via `customtkinter`) for:
    -   Admin login for initial MQTT server configuration.
    -   Selection of biometric authentication methods (Face, Fingerprint, ID Card).
    -   Displaying recognition status and user information.
    -   Viewing and deleting locally stored biometric records.
9.  **Local SQLite Database**: Stores user information, biometric templates (face, fingerprint), validity rules, MAC address, unique finger positions on the sensor, and an MQTT message outbox for reliable message delivery.
10. **Persistent Configuration**: Saves MQTT server details and authentication token locally in a JSON file.

---

## Hardware Overview

1.  **Raspberry Pi** (Model 3/4 or similar)
2.  **Touch LCD** (e.g., 7-inch, resolution ~800×480 or 1024x600) compatible with `customtkinter`.
3.  **Pi Camera Module** (e.g., V2, V3) connected via CSI interface.
4.  **Fingerprint Sensor** (e.g., R307, R503) connected via Serial (e.g., `/dev/ttyAMA0`).
5.  **Door Sensor** (e.g., MC-38 magnetic switch) connected to GPIO:
    -   Signal: GPIO 17 (as per `main.py` default for `Door` class)
6.  **Door Lock Relay/Driver** connected to GPIO:
    -   Control: GPIO 27 (as per `main.py` default for `Door` class)
7.  **SOS Button** (Push button) connected to GPIO:
    -   Signal: GPIO 5
8.  **Manual Open Door Button** (Push button) connected to GPIO:
    -   Signal: GPIO 13
9.  **Buzzer** connected to GPIO:
    -   Control: GPIO 26
10. **Network Connection** (Ethernet or Wi-Fi)

---

## Software Overview

1.  **Operating System**: Raspberry Pi OS (e.g., Bullseye or later).
2.  **Python 3** (e.g., Python 3.9+).
3.  **MQTT Client**: `paho-mqtt` for Python.
4.  **Face Recognition & Image Processing**:
    -   `insightface` (e.g., `buffalo_l` model)
    -   `onnxruntime` (CPUExecutionProvider)
    -   `opencv-python` (`cv2`)
    -   `Pillow` (PIL Fork)
    -   `numpy`
    -   `scikit-learn` (for `cosine_similarity`)
    -   `picamera2`
5.  **Fingerprint Sensor**: `pyfingerprint` library.
6.  **GUI**: `customtkinter`.
7.  **GPIO Control**: `RPi.GPIO`.
8.  **Environment Variables**: `python-dotenv`.
9.  **Database**: `sqlite3` (Python built-in).
10. **HTTP Requests**: `requests` (for token retrieval).

---

## System Architecture

Below is a simplified architecture diagram illustrating how each component connects:

![image](https://github.com/user-attachments/assets/444fbcb1-7f25-48b7-b605-ebe268a54a3d)

-   The Raspberry Pi communicates with the **MQTT Broker** (potentially over a secure TLS connection, configurable).
-   The **Server** (external component) processes device data, stores records in its **Database**, and typically exposes a **Web Client** for real-time monitoring and management.
-   The Raspberry Pi maintains its own local **SQLite Database** for biometric data, user validity, and MQTT outbox.

---

## Flow Diagram

Below is the high-level flow of how the system operates:

![image](https://github.com/user-attachments/assets/4f988d22-fba5-43a9-8588-7a5b7e933d2d)

*(Note: The "Enter MAC Address" step in the diagram is replaced by Admin Login and MQTT Server Configuration in the current implementation if `mqtt_config.json` is missing.)*

---

## Installation & Setup

1.  **Install Raspberry Pi OS** on your Pi and ensure you have Python 3 installed.
2.  **Enable Interfaces**:
    -   Camera: via `sudo raspi-config` -> Interface Options -> Camera.
    -   Serial Port: via `sudo raspi-config` (ensure serial console is disabled and serial port hardware is enabled if using `/dev/ttyAMA0` for fingerprint).
    -   I2C: (Not explicitly used by core features in provided code, but common for LCD touch).
3.  **Install System Dependencies**:
    ```bash
    sudo apt-get update
    sudo apt-get install -y python3-pip git libatlas-base-dev libopenjp2-7 # For OpenCV/Numpy
    # For Picamera2 and related (on Bullseye/Bookworm)
    sudo apt-get install -y python3-opencv libcamera-apps python3-picamera2
    # For other Pillow image formats
    sudo apt-get install -y libjpeg-dev zlib1g-dev libtiff5-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python3-tk
    ```
4.  **Create a Project Directory and Virtual Environment (Recommended)**:
    ```bash
    mkdir access_control && cd access_control
    python3 -m venv .venv
    source .venv/bin/activate
    ```
5.  **Install Python Libraries**:
    ```bash
    pip install paho-mqtt Pillow customtkinter opencv-python RPi.GPIO python-dotenv pyfingerprint insightface onnxruntime numpy scikit-learn picamera2 requests
    ```
    *(Note: `insightface` and `onnxruntime` installation can sometimes be tricky. Refer to their official documentation if issues arise. You might need a specific `onnxruntime` version compatible with your Pi's architecture.)*
6.  **Place Project Files**: Copy all your Python files (`main.py`, `mqtt.py`, etc.) and the `images` directory into this project directory.
7.  **Create `.env` file** for admin credentials:
    ```
    ADMIN_USERNAME=your_admin_username
    ADMIN_PASSWORD=your_admin_password
    ```
8.  **Hardware Connections**: Connect all hardware components (camera, fingerprint sensor, buttons, buzzer, door lock, door sensor, LCD) to the Raspberry Pi according to the "Hardware Overview" section and your chosen GPIO pins.

---

## Usage

### First Run
1.  On the first execution, or if `mqtt_config.json` is missing, the system will display an **Admin Login** screen.
2.  Enter the admin credentials (defined in `.env`).
3.  Upon successful login, you will be prompted to enter **MQTT Server Configuration**:
    -   Domain (for HTTP Token API, e.g., `http://api.example.com`)
    -   HTTP Port (if Domain is not a full URL, or if using broker IP for token API)
    -   MQTT Broker address (IP or hostname)
    -   MQTT Port
4.  The device will attempt to retrieve an MQTT token via HTTP and then connect to the MQTT broker. This configuration (including the retrieved token and username) will be saved in `mqtt_config.json`.

### Subsequent Runs
1.  The device reads the saved configuration from `mqtt_config.json` (including broker, port, token, username) and attempts MQTT connection.
2.  The main menu is displayed with three options:
    -   **Khuôn Mặt (Face Recognition)**
    -   **Vân Tay (Fingerprint)**
    -   **Thẻ CCCD (ID Card)** (UI option, backend logic may be a placeholder)
3.  A **"Cài Đặt" (Settings)** button allows re-entering the Admin Login to reconfigure MQTT settings.
4.  A **Sync button** allows manually requesting a full data sync from the server.

### Biometric Authentication
-   **Face Recognition**: Select "Khuôn Mặt". The camera feed will appear. Position your face in front of the camera. Upon successful recognition and validation against the local database, the system will grant access.
-   **Fingerprint**: Select "Vân Tay". Follow on-screen prompts to place your finger on the sensor. If a match is found on the sensor and validated against the local database, access is granted.
-   **ID Card**: Selecting this option will currently lead to a placeholder screen or basic functionality, awaiting full implementation.

### Door Lock & Alerts
-   Upon successful biometric authentication, the door lock is triggered to open for a configured duration (e.g., 10 seconds) and then automatically closes.
-   Pressing the **SOS Button** activates the buzzer and sends an `SOS_ACTIVATED` alert via MQTT.
-   Pressing the **Manual Open Door Button** opens the door for the configured duration or until the button is released (if released sooner).
-   The **Door Sensor** status (open/closed) is monitored and reported via MQTT.

### Server-Side Operations (Assumed)
-   The server receives health checks, recognition events, SOS alerts, and door status updates.
-   The server can push biometric data commands (add/update/delete users) to the device, which are processed and stored in the local SQLite database and on the fingerprint sensor.
-   The server logs events and provides a monitoring interface (e.g., a web client).

---

## License

This project is distributed under the [MIT License](https://opensource.org/licenses/MIT). You are free to use and modify it for your own needs. See the `LICENSE` file (if one exists in your project) or assume standard MIT terms.

---

**Thank you for using this Access Control & Monitoring System!**
For additional questions, suggestions, or troubleshooting, please refer to the project's issue tracker or contact the maintainers.

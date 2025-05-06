
-   The **Enrollment Device** first contacts the **Backend Server** via HTTP to get MQTT credentials.
-   It then connects to the **MQTT Broker**.
-   Enrolled user data and biometrics are packaged and sent via MQTT to a topic associated with the **Target Access Control Device**.
-   The local SQLite database acts as an outbox for MQTT messages.

---

## Flow Diagram

1.  **Device Boot & Initialization**:
    -   Check for `mqtt_enroll_config.json`.
    -   If **missing or invalid**:
        -   Display MQTT Configuration screen.
        -   Operator enters Broker, MQTT Port, HTTP API Port.
        -   Device requests MQTT token from Backend Server via HTTP using its MAC address.
        -   Server returns MQTT username & token.
        -   Configuration (broker, ports, username, token) is saved to `mqtt_enroll_config.json`.
    -   If **present and valid**:
        -   Load configuration (including saved token).
    -   Initialize `MQTTEnrollManager` and attempt connection to MQTT Broker.
    -   Display Main Enrollment Screen.

2.  **Main Enrollment Screen**:
    -   Operator selects Target Room (maps to MAC).
    -   Operator enters User ID, Name, Validity Dates/Times, Active Days.
    -   Operator chooses to enroll Face or Fingerprint.

3.  **Biometric Enrollment Sub-Screen (Face/Fingerprint)**:
    -   Dedicated UI guides the operator.
    -   Face: Camera preview, capture, process.
    -   Fingerprint: Sensor interaction, scan, process.
    -   On success, biometric template (and face image) is stored in memory.
    -   Returns to Main Enrollment Screen, updating status.

4.  **Send Enrollment Data**:
    -   Operator clicks "ĐĂNG KÝ" (Register/Send).
    -   Input validation is performed (all required fields filled, correct formats).
    -   Enrollment package (User Info + Biometric Templates + Access Rules) is created.
    -   `MQTTEnrollManager` publishes the package to `iot/server/push_biometric/{target_mac_address}`.
        -   If MQTT connected: Sends directly.
        -   If MQTT disconnected: Queues to local SQLite outbox.
    -   Confirmation message shown to operator.
    -   Enrollment state is reset for the next user (new Bio ID generated, fields cleared, biometric data cleared from memory).

5.  **MQTT Outbox Flushing**:
    -   When MQTT connection is (re-)established, `MQTTEnrollManager` attempts to send any pending messages from the outbox.

6.  **Periodic Health Check**:
    -   Device sends health check messages to `iot/devices/healthcheck` at regular intervals.

---

## Installation & Setup

1.  **Install Raspberry Pi OS** on your Pi and ensure you have Python 3 installed.
2.  **Enable Interfaces**:
    -   Camera: via `sudo raspi-config` -> Interface Options -> Camera.
    -   Serial Port: via `sudo raspi-config` (ensure serial console is disabled and serial port hardware is enabled if using `/dev/ttyAMA0` or `/dev/ttyAMA4` for fingerprint).
3.  **Install System Dependencies**:
    ```bash
    sudo apt-get update
    sudo apt-get install -y python3-pip git libatlas-base-dev libopenjp2-7
    sudo apt-get install -y python3-opencv libcamera-apps python3-picamera2
    sudo apt-get install -y libjpeg-dev zlib1g-dev libtiff5-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python3-tk
    ```
4.  **Create a Project Directory and Virtual Environment (Recommended)**:
    ```bash
    mkdir enrollment_device && cd enrollment_device
    python3 -m venv .venv
    source .venv/bin/activate
    ```
5.  **Install Python Libraries**:
    ```bash
    pip install paho-mqtt Pillow customtkinter opencv-python RPi.GPIO pyfingerprint insightface onnxruntime numpy picamera2 requests
    ```
    *(Note: `insightface` and `onnxruntime` installation can be specific. Refer to their official documentation. Ensure `pyfingerprint` is compatible with your sensor and Python version.)*
6.  **Place Project Files**: Copy all your Python files (`main_enroll.py`, `mqtt_enroll.py`, `face_enroll.py`, `fingerprint_enroll.py`, `database_enroll.py`) and the `images` directory into this project directory.
7.  **Hardware Connections**: Connect all hardware components (camera, fingerprint sensor, LCD) to the Raspberry Pi. Ensure the fingerprint sensor is connected to the serial port defined in `FINGERPRINT_PORT` (`main_enroll.py`).

---

## Usage

### First Run
1.  On the first execution, or if `mqtt_enroll_config.json` is missing/invalid, the system will display an **MQTT Server Configuration** screen.
2.  Enter:
    -   MQTT Broker address (IP or hostname).
    -   MQTT Port (e.g., 1883, 8883 for TLS).
    -   HTTP Port (for the backend API that provides MQTT tokens, e.g., 8080).
    -   *(Optionally, a Domain can be entered if the token API is hosted on a different domain than the MQTT broker, but current logic primarily uses Broker IP for token API if domain is not set).*
3.  The device will attempt to retrieve an MQTT token/username via HTTP from the configured HTTP Port on the Broker's IP (or Domain if specified).
4.  The configuration (broker, ports, and retrieved MQTT username/token) will be saved in `mqtt_enroll_config.json`.
5.  The device will then attempt to connect to the MQTT broker.

### Subsequent Runs
1.  The device reads the saved configuration from `mqtt_enroll_config.json`.
2.  It attempts to connect to the MQTT broker using the saved credentials.
3.  The **Main Enrollment Screen** is displayed.
4.  A **"Cài Đặt" (Settings)** button allows re-entering the MQTT Configuration screen (this will delete the current config file).

### Enrollment Process
1.  On the Main Enrollment Screen:
    -   Select the **Target Room** from the dropdown (this determines the MAC address of the receiving access control device).
    -   A **Bio ID** is auto-generated.
    -   Enter the **ID Number** (Số CCCD).
    -   Enter the **Person's Name** (Họ và Tên).
    -   Enter **Validity Period**: "Từ Ngày" (From Date), "Đến Ngày" (To Date), "Từ Giờ" (From Time), "Đến Giờ" (To Time) in YYYY-MM-DD and HH:MM:SS formats.
    -   Select **Active Days** using the checkboxes (T2 for Monday, ..., CN for Sunday).
2.  Click **"KHUÔN MẶT" (Face)** or **"VÂN TAY" (Fingerprint)** to capture biometrics.
    -   Follow on-screen instructions.
    -   Upon successful capture, the status label below the button will update to "Đăng ký thành công" and turn green.
3.  Once all required information is entered and at least one biometric is enrolled, click **"ĐĂNG KÝ" (Register/Send)**.
    -   The system performs input validation.
    -   If valid, the enrollment package is sent (or queued if offline) via MQTT.
    -   A success/queued message is shown.
    -   The form resets for the next enrollment (new Bio ID, cleared fields, biometric statuses reset).

---

## License

This project is assumed to be under a permissive license like MIT unless otherwise specified. You are free to use, modify, and distribute it.

---

**Thank you for using the Enrollment Device!**

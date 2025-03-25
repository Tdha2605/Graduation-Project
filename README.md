# Access Control & Monitoring System

This project implements an **access control device** based on a Raspberry Pi. The device can perform **face recognition**, **vibration detection**, and **temperature/infrared monitoring**. It communicates with a server via **MQTT**, sending status updates, receiving commands, and integrating with a door lock system. The server stores data in a database and provides a web client for monitoring.

---

## Table of Contents

1. [Introduction](#introduction)  
2. [Features](#features)  
3. [Hardware Overview](#hardware-overview)  
4. [Software Overview](#software-overview)  
5. [System Architecture](#system-architecture)  
6. [Flow Diagram](#flow-diagram)  
7. [Installation & Setup](#installation--setup)  
8. [Usage](#usage)  
9. [License](#license)  

---

## Introduction

In many facilities, controlling and monitoring access is critical. This project addresses that need by providing:

- A **touch-based interface** on a 7-inch LCD for user interaction.  
- A **camera module** for face recognition.  
- Multiple sensors (e.g., infrared array, vibration) to enhance security.  
- A **door lock** and a **buzzer** connected to the GPIO for real-time door control and alerts.  
- Communication via **MQTT** with a backend server for centralized logging, monitoring, and management.  

The end goal is a self-contained, Internet-connected device that manages physical access while integrating with a broader system (web client, database, etc.).

---

## Features

1. **Face Recognition**: Uses the Raspberry Pi Camera Module to authenticate users.  
2. **Infrared Array Sensor (AMG8833)**: Monitors temperature or detects movement/heat signatures.  
3. **Vibration Sensor**: Detects tampering or forced entry attempts.  
4. **MQTT Communication**: Publishes and subscribes to topics for:  
   - Health checks  
   - Device registration  
   - Commands from the server  
5. **Door Lock Control**: Engages or disengages a physical lock via GPIO pins.  
6. **Buzzer Alerts**: Activates buzzer alarms based on system events or alerts.  
7. **7-inch Touch LCD**: Provides a user interface for local control (entering MAC address on first run, selecting biometric methods, etc.).

---

## Hardware Overview

1. **Raspberry Pi** (Model 3/4 or similar)  
2. **7-inch Touch LCD** (HDMI interface, I2C for touch, resolution ~800×480)  
3. **Pi Camera Module V3** (or V2) connected via CSI interface  
4. **Infrared Array Sensor (AMG8833)** connected via I2C  
5. **Vibration Sensor** connected to a GPIO pin  
6. **Buzzer** connected to a GPIO pin  
7. **Door Lock** (electronic lock or solenoid) connected to GPIO (with appropriate driver/transistor/relay)  
8. **Network Connection** (Ethernet or Wi-Fi)

---

## Software Overview

1. **Operating System**: Raspberry Pi OS (Bullseye or later).  
2. **Python 3** for main scripts.  
3. **MQTT Client**: [paho-mqtt](https://pypi.org/project/paho-mqtt/) for Python.  
4. **OpenCV** (optional if face recognition is implemented in Python) or another library for image processing.  
5. **PIL/Pillow** for image handling in the Tkinter interface.  
6. **Tkinter** for the GUI on the 7-inch LCD.

---

## System Architecture

Below is a simplified architecture diagram illustrating how each component connects:

![image](https://github.com/user-attachments/assets/444fbcb1-7f25-48b7-b605-ebe268a54a3d)

- The Raspberry Pi communicates with the **MQTT Broker** over a secure TLS connection.  
- The **Server** processes device data, stores records in the **Database**, and exposes a **Web Client** for real-time monitoring.

---

## Flow Diagram

Below is the high-level flow of how the system operates:

![image](https://github.com/user-attachments/assets/4f988d22-fba5-43a9-8588-7a5b7e933d2d)


---

## Installation & Setup

1. **Install Raspberry Pi OS** on your Pi and ensure you have Python 3.  
2. **Enable the Camera** interface via `raspi-config` (if using the Pi Camera).  
3. **Install Dependencies**:
   ```bash
   sudo apt-get update
   sudo apt-get install python3-pip python3-opencv python3-pil
   pip3 install paho-mqtt
   # Install other libs if needed
Usage
First Run:

The system checks for a stored MAC in device_mac.txt.

If none is found, you are prompted to enter a MAC address on the touch screen keyboard.

The device sends the MAC to the server for registration.

(If this is a brand-new device, the server should respond with a success message.)

Subsequent Runs:

The device reads the saved MAC and immediately attempts MQTT connection.

It automatically shows the main menu with three options:

Face Recognition

Fingerprint

ID Card

Selecting a Biometric Method:

For demonstration, each method just shows a pop-up message.

In a real deployment, you would integrate actual face/fingerprint/ID scanning code here.

Door Lock & Alerts:

Upon successful recognition, the device can trigger the door lock to open.

If the vibration sensor or IR sensor detects unusual activity, it can sound the buzzer or notify the server.

Monitoring:

The server logs all events in a database.

The web client can display device status, user entries, and sensor alerts in real time.

License
This project is distributed under the MIT License. You are free to use and modify it for your own needs. See the LICENSE file for details.

Thank you for using our Access Control & Monitoring System!
For additional questions, suggestions, or troubleshooting, please open an issue or contact the project maintainers.



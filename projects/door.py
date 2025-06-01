import RPi.GPIO as GPIO
from datetime import datetime, timezone

class Door:
    def __init__(self, sensor_pin, relay_pin, debounce_time=300, relay_active_high=False, mqtt_publish_callback=None):
        self.sensor_pin = sensor_pin
        self.relay_pin = relay_pin
        self.mqtt_publish_callback = mqtt_publish_callback
        self.debounce_time = debounce_time
        self.last_state = None
        self.relay_active_high = relay_active_high

        # Clean GPIo state
        try:
            GPIO.cleanup(self.sensor_pin)
        except Exception as e:
            print("Cleanup error on sensor pin (may be expected if not set up):", e)
        try:
            GPIO.cleanup(self.relay_pin)
        except Exception as e:
            print("Cleanup error on relay pin (may be expected if not set up):", e)

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        GPIO.setup(self.relay_pin, GPIO.OUT)
        # đảm bảo cửa luôn đóng khi khởi động lại uwnhgs dụng
        if self.relay_active_high:
            GPIO.output(self.relay_pin, GPIO.LOW)
        else:
            GPIO.output(self.relay_pin, GPIO.HIGH)

        GPIO.add_event_detect(
            self.sensor_pin,
            GPIO.BOTH,
            callback=self._callback,
            bouncetime=self.debounce_time
        )

    def _callback(self, channel):
        # Khi có thay đổi, gửi thông tin trangjt thái cửa lên server
        state = "OPEN" if GPIO.input(self.sensor_pin) == GPIO.HIGH else "CLOSE"
        if state != self.last_state:
            self.last_state = state
            payload = {
                "MacAddress": None,  
                "DeviceTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "DoorStatus": state,
                "Abnormal": False,
            }
            self.mqtt_publish_callback(payload)

    def open_door(self):
        if self.relay_active_high:
            GPIO.output(self.relay_pin, GPIO.HIGH)
        else:
            GPIO.output(self.relay_pin, GPIO.LOW)

    def close_door(self):
        if self.relay_active_high:
            GPIO.output(self.relay_pin, GPIO.LOW)
        else:
            GPIO.output(self.relay_pin, GPIO.HIGH)

    def cleanup(self):
        GPIO.remove_event_detect(self.sensor_pin)
        GPIO.cleanup(self.sensor_pin)
        GPIO.cleanup(self.relay_pin)

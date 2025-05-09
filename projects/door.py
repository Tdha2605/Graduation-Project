import RPi.GPIO as GPIO
from datetime import datetime, timezone

class Door:
    def __init__(self, sensor_pin, relay_pin, debounce_time=300, relay_active_high=False, mqtt_publish_callback=None):
        """
        Initialize the door sensor and relay.

        :param sensor_pin: BCM GPIO pin number for the door sensor (e.g., MC-38 sensor).
        :param relay_pin: BCM GPIO pin number for controlling the door relay.
        :param mqtt_publish_callback: A callback function that accepts a payload dict.
        :param debounce_time: Debounce time in milliseconds.
        :param relay_active_high: If True, relay is activated when GPIO output is HIGH. Default is False.
        """
        self.sensor_pin = sensor_pin
        self.relay_pin = relay_pin
        self.mqtt_publish_callback = mqtt_publish_callback
        self.debounce_time = debounce_time
        self.last_state = None
        self.relay_active_high = relay_active_high

        # Clean up sensor and relay pins in case they're already in use.
        try:
            GPIO.cleanup(self.sensor_pin)
        except Exception as e:
            print("Cleanup error on sensor pin (may be expected if not set up):", e)
        try:
            GPIO.cleanup(self.relay_pin)
        except Exception as e:
            print("Cleanup error on relay pin (may be expected if not set up):", e)

        # Set up GPIO using BCM numbering.
        GPIO.setmode(GPIO.BCM)
        # Sensor pin setup (with pull-up resistor).
        GPIO.setup(self.sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        # Relay pin setup as output.
        GPIO.setup(self.relay_pin, GPIO.OUT)
        # Set relay to OFF state.
        if self.relay_active_high:
            GPIO.output(self.relay_pin, GPIO.LOW)
        else:
            GPIO.output(self.relay_pin, GPIO.HIGH)

        # Add event detection for sensor changes.
        GPIO.add_event_detect(
            self.sensor_pin,
            GPIO.BOTH,
            callback=self._callback,
            bouncetime=self.debounce_time
        )

    def _callback(self, channel):
        # Assume: GPIO.HIGH means door is OPEN; GPIO.LOW means door is CLOSED.
        state = "OPEN" if GPIO.input(self.sensor_pin) == GPIO.HIGH else "CLOSE"
        if state != self.last_state:
            self.last_state = state
            payload = {
                "MacAddress": None,  # To be filled by the external callback.
                "DeviceTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "DoorStatus": state,
                "Abnormal": False,
            }
            # Invoke the callback with the payload.
            self.mqtt_publish_callback(payload)

    def open_door(self):
        """Activate the relay to open the door."""
        if self.relay_active_high:
            GPIO.output(self.relay_pin, GPIO.HIGH)
        else:
            GPIO.output(self.relay_pin, GPIO.LOW)

    def close_door(self):
        """Deactivate the relay to close the door."""
        if self.relay_active_high:
            GPIO.output(self.relay_pin, GPIO.LOW)
        else:
            GPIO.output(self.relay_pin, GPIO.HIGH)

    def cleanup(self):
        """Remove event detection and clean up the GPIO pins."""
        GPIO.remove_event_detect(self.sensor_pin)
        GPIO.cleanup(self.sensor_pin)
        GPIO.cleanup(self.relay_pin)

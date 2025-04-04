import board
import busio
import adafruit_amg88xx
import numpy as np
from scipy.ndimage import zoom
from matplotlib import cm
from PIL import Image

# === CONFIGURATION PARAMETERS ===
DEFAULT_ZOOM_FACTOR = 40      # Upscale factor: 8x8 -> 32x32
DEFAULT_VMIN = 20.0           # Minimum temperature for normalization (°C)
DEFAULT_VMAX = 40.0           # Maximum temperature for normalization (°C)
DEFAULT_DETECTION_THRESHOLD = 32.0  # Temperature threshold for detection (°C)
DEFAULT_MIN_HOT_PIXELS = 1          # Minimum number of pixels above threshold to detect a person

class AMG8833Sensor:
    def __init__(self, zoom_factor=DEFAULT_ZOOM_FACTOR, vmin=DEFAULT_VMIN, vmax=DEFAULT_VMAX,
                 detection_threshold=DEFAULT_DETECTION_THRESHOLD, min_hot_pixels=DEFAULT_MIN_HOT_PIXELS):
        """
        Initialize the AMG8833 sensor and detection configuration.
        
        :param zoom_factor: Factor to upscale the 8x8 data for display.
        :param vmin: Minimum temperature for image normalization.
        :param vmax: Maximum temperature for image normalization.
        :param detection_threshold: Temperature threshold for detecting a person.
        :param min_hot_pixels: Minimum count of pixels above threshold to declare detection.
        """
        i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_amg88xx.AMG88XX(i2c)
        self.zoom_factor = zoom_factor
        self.vmin = vmin
        self.vmax = vmax
        self.detection_threshold = detection_threshold
        self.min_hot_pixels = min_hot_pixels

    def get_thermal_data(self):
        """
        Reads the raw 8x8 thermal data from the sensor.
        
        :return: A NumPy array of shape (8,8) with temperature values.
        """
        data = np.array(self.sensor.pixels)
        return data

    def get_average_temperature(self):
        """
        Computes the average temperature of the 8x8 grid.
        
        :return: The mean temperature.
        """
        data = self.get_thermal_data()
        return np.mean(data)

    def get_thermal_map_image(self):
        """
        Generates a colorized thermal map image.
        
        The raw 8x8 data is upscaled using the defined zoom factor,
        normalized between vmin and vmax, and a colormap is applied.
        
        :return: A PIL Image of the thermal map.
        """
        data = self.get_thermal_data()
        upscaled = zoom(data, self.zoom_factor)
        norm = np.clip((upscaled - self.vmin) / (self.vmax - self.vmin), 0, 1)
        colormap = cm.get_cmap("inferno")
        colored = colormap(norm)
        rgb_array = (colored[:, :, :3] * 255).astype(np.uint8)
        img = Image.fromarray(rgb_array)
        return img

    def detect_person(self):
        """
        Detects the presence of a person based on the number of pixels
        exceeding the defined temperature threshold.
        
        :return: A tuple (detected: bool, hot_pixel_count: int)
                 where 'detected' is True if the count of pixels above the 
                 detection threshold meets or exceeds the minimum, and 'hot_pixel_count'
                 is the number of pixels above the threshold.
        """
        data = self.get_thermal_data()
        hot_pixel_count = np.sum(data > self.detection_threshold)
        detected = hot_pixel_count >= self.min_hot_pixels
        return detected, hot_pixel_count

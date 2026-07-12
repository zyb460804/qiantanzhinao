"""
HX711 Load Cell Amplifier driver for Raspberry Pi.
Reads weight from a pressure sensor via GPIO.

Runs in SIMULATION mode when GPIO hardware is unavailable
(no RPi.GPIO / lgpio installed), so the logic is unit-testable on
any machine. Set HX711_SIMULATE=0 to force real GPIO.
"""

import os
import random
import time

_SIMULATE = os.getenv("HX711_SIMULATE", "1") != "0"


class HX711Sensor:
    """HX711 24-bit ADC weight sensor driver (DT/SCK via GPIO)."""

    def __init__(
        self,
        dout_pin: int = 5,
        pd_sck_pin: int = 6,
        known_weight_g: float | None = None,
        simulate: bool | None = None,
    ):
        self.dout_pin = dout_pin
        self.pd_sck_pin = pd_sck_pin
        self.offset = 0.0          # raw tare offset
        self.scale = 1.0           # raw -> grams
        self._gpio = None
        self.simulate = _SIMULATE if simulate is None else simulate
        if not self.simulate:
            self._init_gpio()
        # 若配置提供了标准砝码重量，初始化时自动去皮+标定
        if known_weight_g:
            self.tare(times=5)
            self.calibrate(known_weight_grams=known_weight_g, times=5)

    def _init_gpio(self):
        try:
            import RPi.GPIO as GPIO  # type: ignore
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.dout_pin, GPIO.IN)
            GPIO.setup(self.pd_sck_pin, GPIO.OUT)
            self._gpio = GPIO
        except Exception as e:  # noqa: BLE001
            self.simulate = True
            print(f"[HX711] GPIO 不可用，进入模拟模式: {e}")

    def _read_raw(self) -> int:
        """Read 24-bit value from HX711 (simulated if no hardware)."""
        if self.simulate or self._gpio is None:
            # Stable reading around a base with small jitter
            return int(500_000 + random.uniform(-200, 200))
        GPIO = self._gpio
        GPIO.output(self.pd_sck_pin, False)
        while GPIO.input(self.dout_pin):
            pass
        count = 0
        for _ in range(24):
            GPIO.output(self.pd_sck_pin, True)
            count = (count << 1) | GPIO.input(self.dout_pin)
            GPIO.output(self.pd_sck_pin, False)
        # 25th pulse sets gain for next read (channel A, gain 128)
        GPIO.output(self.pd_sck_pin, True)
        GPIO.output(self.pd_sck_pin, False)
        if count & 0x800000:
            count |= ~0xFFFFFF
        return count

    def tare(self, times: int = 10) -> float:
        """Set current weight as zero (tare)."""
        readings = [self._read_raw() for _ in range(times)]
        self.offset = sum(readings) / len(readings)
        return self.offset

    def calibrate(self, known_weight_grams: float, times: int = 10) -> float:
        """Set scale factor using a known weight (grams)."""
        if known_weight_grams <= 0:
            raise ValueError("known_weight_grams must be > 0")
        readings = [self._read_raw() for _ in range(times)]
        raw_avg = sum(readings) / len(readings)
        self.scale = (raw_avg - self.offset) / known_weight_grams
        if self.scale == 0:
            self.scale = 1.0
        return self.scale

    def read_raw_grams(self) -> float:
        """Raw grams before filtering (after tare + calibration)."""
        raw = self._read_raw()
        grams = (raw - self.offset) / self.scale if self.scale != 0 else 0.0
        return max(0.0, grams)

    def read_weight_grams(self, samples: int = 5) -> float:
        """
        Read a stable weight in grams (after tare + calibration).

        Applies a simple moving-average filter over `samples` reads for stability.
        """
        if samples <= 1:
            return round(self.read_raw_grams(), 1)
        values = [self.read_raw_grams() for _ in range(samples)]
        return round(sum(values) / len(values), 1)

    def cleanup(self):
        """Release GPIO resources (real hardware only)."""
        if self._gpio is not None:
            try:
                self._gpio.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._gpio = None

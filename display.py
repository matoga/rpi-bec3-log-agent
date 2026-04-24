import logging, math, time

log = logging.getLogger(__name__)

_RGB_ADDR = 0x30
_WHITE  = (255, 255, 255)
_YELLOW = (255, 255,   0)
_RED    = (255,   0,   0)

_EXPECTED = {"humidity_pct", "temperature_c", "light_raw"}
_SPIN = ['/', '-', '\\', '|']


def _ok(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


class Display:
    def __init__(self):
        import smbus2  # deferred — not available on dev machines
        from grove.display.jhd1802 import JHD1802
        self._lcd = JHD1802()
        self._bus = smbus2.SMBus(1)
        self._bus.write_byte_data(_RGB_ADDR, 0x00, 0x07)
        time.sleep(0.01)
        self._bus.write_byte_data(_RGB_ADDR, 0x04, 0x15)
        self._tick = 0
        log.info("LCD display ready")

    def tick(self):
        """Advance the spinner one step — call every second during idle."""
        spin = _SPIN[self._tick % 4]
        self._tick += 1
        try:
            self._lcd.setCursor(0, 15)
            self._lcd.write(spin)
        except Exception as e:
            log.warning("[lcd] tick error: %s", e)

    def _rgb(self, r, g, b):
        self._bus.write_byte_data(_RGB_ADDR, 0x06, r)
        self._bus.write_byte_data(_RGB_ADDR, 0x07, g)
        self._bus.write_byte_data(_RGB_ADDR, 0x08, b)

    def update(self, ts: str, ok: bool, readings: dict, err=None):
        humi  = readings.get("humidity_pct")
        temp  = readings.get("temperature_c")
        light = readings.get("light_raw")

        parts = []
        if _ok(humi):  parts.append(f"H{round(humi):02d}")
        if _ok(temp):  parts.append(f"T{temp:.1f}")
        if _ok(light):
            scaled = min(99, int(float(light) * 99 / 1023))
            parts.append(f"L{scaled:02d}")

        if ok:
            msg   = f"{ts} OK"
            color = _WHITE if len(parts) == len(_EXPECTED) else _YELLOW
        else:
            code  = str(err) if err else "ERR"
            msg   = f"{ts} F:{code}"
            color = _RED

        spin  = _SPIN[self._tick % 4]
        self._tick += 1
        line1 = f"{msg:<14} {spin}"   # msg in 14 chars, 1 space, spinner = 16
        line2 = " ".join(parts) if parts else "no data"

        try:
            self._lcd.setCursor(0, 0)
            self._lcd.write(line1)
            self._lcd.setCursor(1, 0)
            self._lcd.write(line2[:16].ljust(16))
            self._rgb(*color)
        except Exception as e:
            log.warning("[lcd] write error: %s", e)

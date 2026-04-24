import logging, math, time

log = logging.getLogger(__name__)

_RGB_ADDR = 0x30
_LCD_ADDR = 0x3e

_WHITE  = (255, 255, 255)
_YELLOW = (255, 255,   0)
_RED    = (255,   0,   0)

_EXPECTED = {"humidity_pct", "temperature_c", "light_raw"}

# Spinner as raw byte values.  Slot 0 (0x00) is a custom backslash defined in
# CGRAM during init — the built-in 0x5C maps to ¥ on HD44780 displays.
_SPIN = [ord('|'), ord('/'), ord('-'), 0x00]

# 5×8 backslash bitmap for CGRAM slot 0
_BACKSLASH_ROWS = [0x10, 0x08, 0x04, 0x02, 0x01, 0x00, 0x00, 0x00]


def _ok(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


class Display:
    def __init__(self):
        import smbus2  # deferred — not available on dev machines
        from grove.display.jhd1802 import JHD1802
        self._lcd = JHD1802()
        self._bus = smbus2.SMBus(1)

        # Define custom backslash in CGRAM slot 0
        self._bus.write_byte_data(_LCD_ADDR, 0x00, 0x40)  # set CGRAM addr = slot 0
        time.sleep(0.001)
        for row in _BACKSLASH_ROWS:
            self._bus.write_byte_data(_LCD_ADDR, 0x40, row)
        self._bus.write_byte_data(_LCD_ADDR, 0x00, 0x80)  # return to DDRAM home
        time.sleep(0.001)

        # Init RGB backlight
        self._bus.write_byte_data(_RGB_ADDR, 0x00, 0x07)
        time.sleep(0.01)
        self._bus.write_byte_data(_RGB_ADDR, 0x04, 0x15)

        self._tick = 0
        log.info("LCD display ready")

    def _rgb(self, r, g, b):
        self._bus.write_byte_data(_RGB_ADDR, 0x06, r)
        self._bus.write_byte_data(_RGB_ADDR, 0x07, g)
        self._bus.write_byte_data(_RGB_ADDR, 0x08, b)

    def _write_spinner(self):
        """Write the current spinner char directly via smbus2 at LCD position (0,15)."""
        char_byte = _SPIN[self._tick % 4]
        self._tick += 1
        # Set DDRAM address: row 0, col 15 = 0x80 | 0x0F
        self._bus.write_byte_data(_LCD_ADDR, 0x00, 0x80 | 0x0F)
        self._bus.write_byte_data(_LCD_ADDR, 0x40, char_byte)

    def tick(self):
        """Advance the spinner one step — called every second during idle."""
        try:
            self._write_spinner()
        except Exception as e:
            log.warning("[lcd] tick error: %s", e)

    def working(self):
        """Show X at position 15 while reading sensors / posting."""
        try:
            self._bus.write_byte_data(_LCD_ADDR, 0x00, 0x80 | 0x0F)
            self._bus.write_byte_data(_LCD_ADDR, 0x40, ord('X'))
        except Exception as e:
            log.warning("[lcd] working error: %s", e)

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

        # Write first 15 chars (msg padded + 1 space), then spinner byte directly
        line1_text = f"{msg:<14} "   # 15 chars; spinner goes in position 15 via smbus2
        line2      = " ".join(parts) if parts else "no data"

        try:
            self._lcd.setCursor(0, 0)
            self._lcd.write(line1_text)
            self._write_spinner()
            self._lcd.setCursor(1, 0)
            self._lcd.write(line2[:16].ljust(16))
            self._rgb(*color)
        except Exception as e:
            log.warning("[lcd] write error: %s", e)

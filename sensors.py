import base64, logging, threading, time, zlib
import numpy as np
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class Sensor(ABC):
    """Add a sensor by subclassing this. Implement name and read().
    read() returns {metric: value} or {} on failure. Never raises."""
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def read(self) -> dict[str, float]: ...


class DHT22(Sensor):
    def __init__(self, pin: int = 5):
        import seeed_dht  # deferred so sensors.py can be imported on a dev machine
        self._sensor = seeed_dht.DHT("22", pin)
        log.info("DHT22 on pin D%d", pin)

    @property
    def name(self): return "dht22"

    def read(self) -> dict[str, float]:
        try:
            # seeed_dht returns the last cached value; throw it away and wait the
            # DHT22 minimum sampling interval (2 s) so the next read is fresh
            self._sensor.read()
            time.sleep(2)
            humi, temp = self._sensor.read()
            if humi is None or temp is None:
                log.warning("[dht22] None reading"); return {}
            return {"temperature_c": round(float(temp), 2), "humidity_pct": round(float(humi), 2)}
        except Exception as e:
            log.warning("[dht22] %s", e); return {}


class LightSensor(Sensor):
    def __init__(self, channel: int = 0):
        from grove.adc import ADC  # deferred so sensors.py can be imported on a dev machine
        self._adc = ADC(address=0x08)
        self._channel = channel
        log.info("LightSensor on ADC channel %d", channel)

    @property
    def name(self): return "light"

    def read(self) -> dict[str, float]:
        try:
            val = self._adc.read(self._channel)
            return {"light_raw": round(float(val), 1)}
        except Exception as e:
            log.warning("[light] %s", e); return {}


class PicoScope(Sensor):
    """PicoScope 2204A USB oscilloscope — ps2000 driver.

    Configures the device in block mode to read a single sample per read().
    Stats return the single value, and waveform is empty.
    """

    # mV integer → PS2000_VOLTAGE_RANGE key
    # NOTE: SDK starts at PS2000_20MV (no 10 mV range on 2204A)
    _RANGE_KEY = {
        20: "PS2000_20MV",   50: "PS2000_50MV",
        100: "PS2000_100MV", 200: "PS2000_200MV", 500: "PS2000_500MV",
        1000: "PS2000_1V",   2000: "PS2000_2V",   5000: "PS2000_5V",
        10000: "PS2000_10V", 20000: "PS2000_20V",
    }

    def __init__(self, range_mv_a: int = 100, coupling_a: str = "DC",
                 range_mv_b: int = 100, coupling_b: str = "DC"):
        import ctypes
        from picosdk.ps2000 import ps2000 as ps  # ps2000 (not ps2000a) for 2204A
        self._ctypes       = ctypes
        self._ps           = ps
        
        self._ch_config = {
            "A": {"range_mv": range_mv_a, "coupling": coupling_a.upper()},
            "B": {"range_mv": range_mv_b, "coupling": coupling_b.upper()},
        }

        self._sample_interval_s = 0.1

        self._chandle      = ctypes.c_int16()
        self._max_adc      = ctypes.c_int16()
        self._running      = True

        self._lock         = threading.Lock()
        self._buffers      = {"A": [], "B": []}

        self._open_and_start()
        
        self._thread = threading.Thread(target=self._sample_loop, daemon=True, name="PicoScope-Sampler")
        self._thread.start()

        log.info("PicoScope ch=A,B (block mode 0.1s bg thread)")

    # ------------------------------------------------------------------ helpers

    def _open_and_start(self):
        ctypes, ps = self._ctypes, self._ps

        # --- diagnostics: show USB state before opening ------------------
        try:
            import subprocess
            usb = subprocess.check_output(["lsusb"], stderr=subprocess.DEVNULL,
                                          text=True, timeout=5)
            pico_lines = [l for l in usb.splitlines() if "0ce9" in l.lower() or "pico" in l.lower()]
            log.debug("[picoscope] lsusb pico entries: %s", pico_lines or "(none)")
        except Exception as e:
            log.debug("[picoscope] lsusb unavailable: %s", e)

        # --- open with retry loop ----------------------------------------
        # On first connection the driver uploads firmware; the device then
        # re-enumerates (USB VID:PID changes) and ps2000_open_unit returns 0.
        # On Linux the re-enumeration timing is unpredictable (can be >10 s),
        # so we retry with a generous backoff rather than a fixed sleep.
        MAX_ATTEMPTS = 8
        RETRY_DELAY  = 3.0   # seconds between attempts
        handle = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            handle = ps.ps2000_open_unit()
            log.debug("[picoscope] open attempt %d/%d → handle=%d",
                      attempt, MAX_ATTEMPTS, handle)
            if handle > 0:
                break
            if attempt < MAX_ATTEMPTS:
                log.info("[picoscope] handle=0 (firmware upload / re-enum?) "
                         "— retrying in %.0fs (attempt %d/%d)…",
                         RETRY_DELAY, attempt, MAX_ATTEMPTS)
                time.sleep(RETRY_DELAY)

        if handle <= 0:
            raise RuntimeError(
                f"ps2000_open_unit returned handle={handle} after "
                f"{MAX_ATTEMPTS} attempts ({MAX_ATTEMPTS * RETRY_DELAY:.0f}s)"
            )
        self._chandle = ctypes.c_int16(handle)
        log.info("[picoscope] opened, handle=%d", handle)

        self._max_adc = ctypes.c_int16(32767)  # ps2000 fixed max ADC value

        # Disable all channels first
        ps.ps2000_set_channel(self._chandle, 0, 0, 0, 0)
        ps.ps2000_set_channel(self._chandle, 1, 0, 0, 0)

        # Enable channels
        for ch in ("A", "B"):
            cfg = self._ch_config[ch]
            coupling_val = ps.PICO_COUPLING[cfg["coupling"]]
            range_key    = self._RANGE_KEY.get(cfg["range_mv"], "PS2000_100MV")
            ch_range     = ps.PS2000_VOLTAGE_RANGE[range_key]
            
            ch_enum = ps.PS2000_CHANNEL[f"PS2000_CHANNEL_{ch}"]
            ps.ps2000_set_channel(
                self._chandle, ch_enum,
                1, coupling_val, ch_range,
            )

        # Block mode setup: disable trigger (5 = PS2000_NONE)
        ps.ps2000_set_trigger(self._chandle, 5, 0, 0, 0, 100)

    # --------------------------------------------------------------- Sensor API

    @property
    def name(self): return "picoscope"

    def _sample_loop(self):
        ps, ctypes = self._ps, self._ctypes
        
        N = 1
        timebase   = 8
        oversample = ctypes.c_int16(1)  # passed to run_block, never mutated
        overflow   = ctypes.c_int16(0)  # overrange output from get_values

        bufA = (ctypes.c_int16 * N)()
        bufB = (ctypes.c_int16 * N)()
        cmaxSamples = ctypes.c_int32(N)

        while self._running:
            start_t = time.monotonic()

            timeIndisposed = ctypes.c_int32()
            ps.ps2000_run_block(self._chandle, N, timebase, oversample, ctypes.byref(timeIndisposed))

            ready = ctypes.c_int16(0)
            while ready.value == 0 and self._running:
                ready = ctypes.c_int16(ps.ps2000_ready(self._chandle))
                if ready.value == 0:
                    time.sleep(0.005)
            
            if not self._running:
                break

            n_values = ps.ps2000_get_values(
                self._chandle,
                ctypes.byref(bufA),
                ctypes.byref(bufB),
                None,
                None,
                ctypes.byref(overflow),
                cmaxSamples
            )

            if n_values > 0:
                ts = time.time()  # precise unix timestamp
                with self._lock:
                    for ch, buf in [("A", bufA), ("B", bufB)]:
                        raw_adc = np.ctypeslib.as_array(buf, shape=(n_values,)).copy()
                        range_mv = self._ch_config[ch]["range_mv"]
                        mv_arr  = raw_adc.astype(np.float32) * (range_mv / 32767.0)
                        if mv_arr.size > 0:
                            val = float(mv_arr[0])
                            self._buffers[ch].append((ts, val))

            elapsed = time.monotonic() - start_t
            sleep_t = max(0.0, self._sample_interval_s - elapsed)
            time.sleep(sleep_t)

    def read(self) -> dict:
        try:
            with self._lock:
                bufs = self._buffers
                self._buffers = {"A": [], "B": []}

            result = {}
            for ch in ("A", "B"):
                buf = bufs.get(ch, [])
                n = len(buf)
                if n == 0:
                    log.warning("[picoscope] no data collected for ch %s", ch)
                    continue

                start_time_unix = buf[0][0]
                times_arr = np.array([int(round((pt[0] - start_time_unix) * 1000)) for pt in buf], dtype=np.uint32)
                mv_arr    = np.array([pt[1] for pt in buf], dtype=np.float32)

                # --- statistics --------------------------------------------------
                p5, p95      = float(np.percentile(mv_arr, 5)), float(np.percentile(mv_arr, 95))
                mask         = (mv_arr >= p5) & (mv_arr <= p95)
                trim_mean    = float(np.mean(mv_arr[mask])) if mask.any() else float(np.mean(mv_arr))

                # --- compress payload -------------------------------------------
                c_times = zlib.compress(times_arr.tobytes(), level=6)
                b64_times = base64.b64encode(c_times).decode("ascii")

                mv_arr_pack = mv_arr.astype(np.float16)
                c_samples = zlib.compress(mv_arr_pack.tobytes(), level=6)
                b64_samples = base64.b64encode(c_samples).decode("ascii")

                log.info("[picoscope] ch=%s n=%d median=%.3f mean=%.3f trim_mean=%.3f mV",
                         ch, n, np.median(mv_arr), np.mean(mv_arr), trim_mean)

                result[f"picoscope_ch_{ch.lower()}"] = {
                    "start_time_unix":    start_time_unix,
                    "n_samples":          n,
                    "sample_interval_ms": int(self._sample_interval_s * 1000),
                    "median_mv":          round(float(np.median(mv_arr)), 4),
                    "mean_mv":            round(float(np.mean(mv_arr)),   4),
                    "trim_mean_mv":       round(trim_mean,                4),
                    "trim_min_mv":        round(p5,                       4),
                    "trim_max_mv":        round(p95,                      4),
                    "timestamps_ms_uint32_b64z": b64_times,
                    "samples_float16_b64z":      b64_samples,
                }
            return result
        except Exception as e:
            log.warning("[picoscope] read error: %s", e)
            return {}

    def close(self):
        """Graceful shutdown — call on agent exit."""
        self._running = False
        if hasattr(self, "_thread") and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        try:
            self._ps.ps2000_stop(self._chandle)
            self._ps.ps2000_close_unit(self._chandle)
            log.info("[picoscope] closed")
        except Exception as e:
            log.warning("[picoscope] close error: %s", e)

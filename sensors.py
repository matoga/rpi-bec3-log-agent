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
    """PicoScope 2204A USB oscilloscope — ps2000a driver.

    A background thread streams Channel A (or configured channel) continuously.
    On read(), the accumulated ADC samples are snapshotted, stats are computed,
    the mV waveform is zlib-compressed and base64-encoded, then streaming is
    restarted immediately so the next period begins with minimal gap.
    """

    # mV integer → ps2000a range key
    _RANGE_KEY = {
        10: "PS2000A_10MV",  20: "PS2000A_20MV",  50: "PS2000A_50MV",
        100: "PS2000A_100MV", 200: "PS2000A_200MV", 500: "PS2000A_500MV",
        1000: "PS2000A_1V",  2000: "PS2000A_2V",  5000: "PS2000A_5V",
        10000: "PS2000A_10V", 20000: "PS2000A_20V",
    }

    def __init__(self, channel: str = "A", range_mv: int = 100,
                 coupling: str = "DC", sample_rate_hz: int = 10_000):
        import ctypes
        from picosdk.ps2000a import ps2000a as ps
        from picosdk.functions import adc2mV

        self._ctypes       = ctypes
        self._ps           = ps
        self._adc2mV       = adc2mV
        self._channel_str  = channel.upper()
        self._range_mv     = range_mv
        self._coupling     = coupling
        self._sample_rate_hz = sample_rate_hz

        self._chandle      = ctypes.c_int16()
        self._max_adc      = ctypes.c_int16()
        self._channel_range = None
        self._drv_buf      = None
        self._buf_size     = 2000   # driver ring-buffer length (samples)

        # Chunk list shared between bg thread and read(); protected by _api_lock
        self._chunks: list  = []
        self._streaming     = False
        self._running       = True
        # Held by bg thread while calling the SDK; acquired by read() to gate on it
        self._api_lock = threading.Lock()

        self._open_and_start()
        self._cFuncPtr = self._make_callback()  # keep alive — never GC'd

        self._thread = threading.Thread(
            target=self._bg_thread, daemon=True, name="picoscope-bg"
        )
        self._thread.start()
        log.info("PicoScope ch=%s range=%dmV coupling=%s rate=%dHz",
                 self._channel_str, self._range_mv, self._coupling, self._sample_rate_hz)

    # ------------------------------------------------------------------ helpers

    def _ch_enum(self):
        return self._ps.PS2000A_CHANNEL[f"PS2000A_CHANNEL_{self._channel_str}"]

    def _open_and_start(self):
        ctypes, ps = self._ctypes, self._ps
        ret = ps.ps2000aOpenUnit(ctypes.byref(self._chandle), None)
        # 0 = PICO_OK; 282 = USB3 device on USB2 port (warning, still works)
        if ret not in (0, 282):
            raise RuntimeError(f"ps2000aOpenUnit returned {ret}")

        ps.ps2000aMaximumValue(self._chandle, ctypes.byref(self._max_adc))

        coupling_val = ps.PS2000A_COUPLING[f"PS2000A_{self._coupling}"]
        range_key    = self._RANGE_KEY.get(self._range_mv, "PS2000A_100MV")
        self._channel_range = ps.PS2000A_RANGE[range_key]

        ps.ps2000aSetChannel(
            self._chandle, self._ch_enum(),
            1, coupling_val, self._channel_range, 0.0,
        )

        self._drv_buf = np.zeros(self._buf_size, dtype=np.int16)
        self._register_buffer()
        self._start_streaming()

    def _register_buffer(self):
        ps, ctypes = self._ps, self._ctypes
        ps.ps2000aSetDataBuffers(
            self._chandle, self._ch_enum(),
            self._drv_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
            None, self._buf_size, 0,
            ps.PS2000A_RATIO_MODE["PS2000A_RATIO_MODE_NONE"],
        )

    def _start_streaming(self):
        ps, ctypes = self._ps, self._ctypes
        interval_us = ctypes.c_int32(max(1, 1_000_000 // self._sample_rate_hz))
        ps.ps2000aRunStreaming(
            self._chandle,
            ctypes.byref(interval_us),
            ps.PS2000A_TIME_UNITS["PS2000A_US"],
            0,             # maxPreTriggerSamples
            2_000_000_000, # maxPostTriggerSamples (huge; autoStop=0)
            0,             # autoStop = 0 → run forever
            1,             # downSampleRatio
            ps.PS2000A_RATIO_MODE["PS2000A_RATIO_MODE_NONE"],
            self._buf_size,
        )
        self._streaming = True

    def _make_callback(self):
        drv_buf = self._drv_buf
        chunks  = self._chunks

        def _cb(handle, n_samples, start_idx, overflow,
                trigger_at, triggered, auto_stop, param):
            if n_samples > 0:
                chunks.append(drv_buf[start_idx:start_idx + n_samples].copy())

        return self._ps.StreamingReadyType(_cb)

    # ---------------------------------------------------------- background thread

    def _bg_thread(self):
        ps = self._ps
        while self._running:
            try:
                if self._streaming:
                    with self._api_lock:
                        ps.ps2000aGetStreamingLatestValues(
                            self._chandle, self._cFuncPtr, None
                        )
                time.sleep(0.01)
            except Exception as e:
                log.warning("[picoscope] poll error: %s", e)
                time.sleep(0.5)

    # --------------------------------------------------------------- Sensor API

    @property
    def name(self): return "picoscope"

    def read(self) -> dict:
        try:
            ps = self._ps

            # --- stop streaming and grab the accumulated chunks atomically ---
            self._streaming = False          # signal bg thread to stop polling
            with self._api_lock:             # wait for any in-flight poll to finish
                ps.ps2000aStop(self._chandle)
                chunks = list(self._chunks)
                self._chunks.clear()
                # Restart immediately so next period has no gap
                self._register_buffer()
                self._start_streaming()       # sets self._streaming = True

            if not chunks:
                log.warning("[picoscope] no samples collected this period")
                return {}

            # --- ADC → mV conversion -----------------------------------------
            raw_adc = np.concatenate(chunks)
            mv_arr  = np.array(
                self._adc2mV(raw_adc, self._channel_range, self._max_adc),
                dtype=np.float32,
            )
            if mv_arr.size == 0:
                return {}

            # --- statistics --------------------------------------------------
            p5, p95      = float(np.percentile(mv_arr, 5)), float(np.percentile(mv_arr, 95))
            mask         = (mv_arr >= p5) & (mv_arr <= p95)
            trim_mean    = float(np.mean(mv_arr[mask])) if mask.any() else float(np.mean(mv_arr))

            # --- compress waveform -------------------------------------------
            compressed = zlib.compress(mv_arr.tobytes(), level=6)
            encoded    = base64.b64encode(compressed).decode("ascii")

            log.info("[picoscope] n=%d median=%.3f mean=%.3f trim_mean=%.3f mV",
                     mv_arr.size, np.median(mv_arr), np.mean(mv_arr), trim_mean)

            return {
                "picoscope_ch_a": {
                    "n_samples":    int(mv_arr.size),
                    "median_mv":    round(float(np.median(mv_arr)), 4),
                    "mean_mv":      round(float(np.mean(mv_arr)),   4),
                    "trim_mean_mv": round(trim_mean,                4),
                    "trim_min_mv":  round(p5,                       4),
                    "trim_max_mv":  round(p95,                      4),
                    "samples_b64z": encoded,
                }
            }
        except Exception as e:
            log.warning("[picoscope] read error: %s", e)
            return {}

    def close(self):
        """Graceful shutdown — call on agent exit."""
        self._running = False
        try:
            self._ps.ps2000aStop(self._chandle)
            self._ps.ps2000aCloseUnit(self._chandle)
            log.info("[picoscope] closed")
        except Exception as e:
            log.warning("[picoscope] close error: %s", e)

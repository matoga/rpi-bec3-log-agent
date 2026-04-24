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

    A background thread streams Channel A (or configured channel) continuously.
    On read(), the accumulated ADC samples are snapshotted, stats are computed,
    the mV waveform is zlib-compressed and base64-encoded, then streaming is
    restarted immediately so the next period begins with minimal gap.
    """

    # mV integer → ps2000 range key
    _RANGE_KEY = {
        10: "PS2000_10MV",  20: "PS2000_20MV",  50: "PS2000_50MV",
        100: "PS2000_100MV", 200: "PS2000_200MV", 500: "PS2000_500MV",
        1000: "PS2000_1V",  2000: "PS2000_2V",  5000: "PS2000_5V",
        10000: "PS2000_10V", 20000: "PS2000_20V",
    }

    def __init__(self, channel: str = "A", range_mv: int = 100,
                 coupling: str = "DC", sample_rate_hz: int = 10_000):
        import ctypes
        from picosdk.ps2000 import ps2000 as ps  # ps2000 (not ps2000a) for 2204A
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
        return self._ps.PS2000_CHANNEL[f"PS2000_CHANNEL_{self._channel_str}"]  # ps2000 enum

    def _open_and_start(self):
        ctypes, ps = self._ctypes, self._ps

        # First call uploads firmware; device re-enumerates, handle will be 0
        ps.ps2000_open_unit()
        time.sleep(2)  # wait for firmware upload + re-enumeration

        # Second call actually opens the unit
        handle = ps.ps2000_open_unit()
        if handle <= 0:
            raise RuntimeError(f"ps2000_open_unit failed, handle={handle}")
        self._chandle = ctypes.c_int16(handle)

        self._max_adc = ctypes.c_int16(32767)  # ps2000 fixed max ADC value

        # SDK exposes coupling as PICO_COUPLING with plain 'AC'/'DC' keys (bool-style)
        coupling_val = ps.PICO_COUPLING[self._coupling]
        range_key    = self._RANGE_KEY.get(self._range_mv, "PS2000_100MV")
        self._channel_range = ps.PS2000_VOLTAGE_RANGE[range_key]

        ps.ps2000_set_channel(
            self._chandle, self._ch_enum(),
            1, coupling_val, self._channel_range,
        )

        self._drv_buf = np.zeros(self._buf_size, dtype=np.int16)
        # No ps2000_set_data_buffer — streaming callback receives buffer pointers directly
        self._start_streaming()

    # _register_buffer intentionally absent: ps2000 streaming delivers buffer
    # pointers via the GetOverviewBuffersType callback; no separate registration needed.

    def _start_streaming(self):
        ps, ctypes = self._ps, self._ctypes
        ps.ps2000_run_streaming_ns(
            self._chandle,
            max(100, 1_000_000_000 // self._sample_rate_hz),  # sample interval in ns
            ps.PS2000_TIME_UNITS["PS2000_NS"],
            self._buf_size,
            0,   # autoStop = 0 → run forever
            1,   # downSampleRatio
            self._buf_size,
        )
        self._streaming = True

    def _make_callback(self):
        import ctypes
        chunks = self._chunks
        buf_size = self._buf_size

        # Signature from SDK: (int16_t **overviewBuffers, int16_t overflow,
        #                       uint32_t triggerAt, int16_t triggered,
        #                       int16_t autoStop, uint32_t nValues)
        def _cb(overview_buffers, overflow, trigger_at, triggered, auto_stop, n_values):
            if n_values > 0 and overview_buffers:
                # overview_buffers[0] is the channel A max buffer pointer
                buf_ptr = overview_buffers[0]
                if buf_ptr:
                    n = min(int(n_values), buf_size)
                    chunks.append(np.ctypeslib.as_array(buf_ptr, shape=(n,)).copy())

        return self._ps.GetOverviewBuffersType(_cb)  # correct callback factory name

    # ---------------------------------------------------------- background thread

    def _bg_thread(self):
        ps = self._ps
        while self._running:
            try:
                if self._streaming:
                    with self._api_lock:
                        ps.ps2000_get_streaming_last_values(
                            self._chandle, self._cFuncPtr
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
                ps.ps2000_stop(self._chandle)
                chunks = list(self._chunks)
                self._chunks.clear()
                # Restart immediately so next period has no gap
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
            self._ps.ps2000_stop(self._chandle)
            self._ps.ps2000_close_unit(self._chandle)
            log.info("[picoscope] closed")
        except Exception as e:
            log.warning("[picoscope] close error: %s", e)

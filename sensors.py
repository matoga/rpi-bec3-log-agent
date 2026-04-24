import logging, time
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

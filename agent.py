#!/usr/bin/env python3
import logging, os, signal, sys, time, tomllib
from pathlib import Path
import requests
from sensors import DHT22, LightSensor
from display import Display

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger(__name__)


def load_config():
    path = Path("config/device.toml")
    if not path.exists():
        log.error("config/device.toml not found"); sys.exit(1)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    cfg = data.get("agent", {})
    def get(env_key, cfg_key, required=False, default=None):
        val = os.environ.get(env_key) or cfg.get(cfg_key, default)  # env takes priority
        if required and not val:
            log.error("Missing: %s (or env %s)", cfg_key, env_key); sys.exit(1)
        return val
    return {
        "server_url":    get("TEMP_AGENT_SERVER_URL",       "server_url",    required=True),
        "api_key":       get("TEMP_AGENT_API_KEY",          "api_key",       required=True),
        "instrument_id": get("TEMP_AGENT_INSTRUMENT_ID",    "instrument_id", default="bec3-grove-rpi"),
        "period":        float(get("TEMP_AGENT_PERIOD_SECONDS", "period_seconds", default=60.0)),
        "sensors":       data.get("sensors", {}),
    }


def post(server_url, api_key, instrument_id, readings):
    """Returns (ok: bool, err_code: str | None)."""
    try:
        resp = requests.post(
            server_url,
            json={"instrument_id": instrument_id, "payload": readings},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("✓ HTTP %d  %s", resp.status_code, readings)
        return True, None
    except requests.exceptions.HTTPError:
        log.error("✗ HTTP %d: %s", resp.status_code, resp.text[:200])
        return False, resp.status_code
    except requests.exceptions.ConnectionError as e:
        log.error("✗ Connection: %s", e)
        return False, "CONN"
    except requests.exceptions.Timeout:
        log.error("✗ Timeout")
        return False, "TOUT"


def main():
    cfg = load_config()

    # to add a sensor: import its class from sensors.py, instantiate it here
    sensors = [
        DHT22(pin=cfg["sensors"].get("dht22", {}).get("pin", 5)),
        LightSensor(channel=cfg["sensors"].get("light", {}).get("channel", 0)),
    ]

    try:
        display = Display()
    except Exception as e:
        log.warning("LCD display unavailable: %s", e)
        display = None

    log.info("Starting  instrument=%s  period=%.0fs  sensors=%s",
             cfg["instrument_id"], cfg["period"], [s.name for s in sensors])

    running = True
    def _stop(sig, _frame):
        nonlocal running
        log.info("Signal %d — stopping", sig); running = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        readings = {}
        for s in sensors:
            data = s.read()
            if data:
                log.info("[%s] %s", s.name, data)
                readings.update(data)
            else:
                log.warning("[%s] no data", s.name)

        ts = time.strftime("%H:%M:%S")
        if readings:
            ok, err = post(cfg["server_url"], cfg["api_key"], cfg["instrument_id"], readings)
        else:
            ok, err = False, "NODATA"

        if display:
            display.update(ts, ok, readings, err)

        # 1-second ticks so SIGTERM is handled promptly instead of sleeping the full period
        deadline = time.monotonic() + cfg["period"]
        while running and time.monotonic() < deadline:
            time.sleep(1)

    log.info("Stopped.")


if __name__ == "__main__":
    main()

# BEC3 RPi Environment Logger

Continuously reads temperature, humidity, light level, and oscilloscope waveform
data (PicoScope 2204A) and posts the data to a remote server every 60 seconds.
Status is shown live on the LCD display.

---

## Hardware

| Device | Grove port | Notes |
|---|---|---|
| DHT22 temp/humidity | D5 | digital |
| Grove Light Sensor | A0 | analog |
| Grove LCD RGB Backlight | I2C | address 0x3e / 0x30 |
| Push button | D22 | hardware pull-down |
| PicoScope 2204A | USB | ps2000a driver |

---

## LCD display

```
+----------------+
| 08:30:01 OK   / |   ← line 1: time · status · indicator
| H24 T26.3 L34 P |   ← line 2: sensor readings (P = PicoScope OK)
+----------------+
```

### Line 1 — status indicator (last character)

| Char | Meaning |
|---|---|
| `W` | Working — reading sensors or sending data |
| `/ - \ \|` | Idle — spinning once per second, confirms agent is alive |

### Line 1 — send status

| Display | Meaning |
|---|---|
| `OK` | Data sent successfully |
| `F:404` | Server error — HTTP status code shown |
| `F:CON` | No network or connection refused |
| `F:TMO` | Server did not respond within 10 s |
| `F:ND` | No sensor returned any data |
| `F:ERR` | Unexpected error |

### Line 2 — sensor readings

Only sensors that returned valid data are shown.

| Display | Meaning |
|---|---|
| `H24` | Humidity 24 % |
| `T26.3` | Temperature 26.3 °C |
| `L34` | Light level (0 = dark, 99 = bright) |
| `P` | PicoScope waveform captured and sent |
| `no data` | All sensors failed this cycle |

### Backlight colour

| Colour | Meaning |
|---|---|
| White | All sensors OK, data sent |
| Yellow | At least one sensor missing, data sent |
| Red | Send failed |

### Button (D22)

Press to trigger an immediate reading and send — resets the 60-second timer.

---

## Service

```bash
bash install-service.sh   # install, enable, and start
bash stop-service.sh      # stop and disable
```

```bash
sudo systemctl status rpi-bec3-log-agent   # check status
journalctl -u rpi-bec3-log-agent -f        # follow logs
```

The agent starts automatically on boot once installed.

---

## First-time setup

```bash
# 1. Clone
git clone <repo-url> ~/rpi-bec3-log-agent
cd ~/rpi-bec3-log-agent

# 2. Install seeed_dht (from grove.py repo, already cloned at ~/grove.py)
cd ~/grove.py && sudo pip3 install . --break-system-packages && cd ~/rpi-bec3-log-agent

# 3. Install dependencies
pip3 install requests smbus2 numpy picosdk

# 4. Configure
cp config/device.toml.example config/device.toml
nano config/device.toml   # set server_url and api_key

# 5. Run manually to verify
python3 agent.py

# 6. Install as a service
bash install-service.sh
```

---

## PicoScope 2204A — installation on RPi 5 (ARM64)

The `picosdk` Python package is a thin `ctypes` shim; it needs the native
`libps2000a` shared library from Pico Technology's Linux repository.

```bash
# Add the Pico Technology apt repository and install the driver
wget -qO - https://labs.picotech.com/debian/pool/main/p/picotech-apt-config/picotech-apt-config_1.0.0-3r1_all.deb \
  | sudo dpkg -i /dev/stdin
sudo apt-get update
sudo apt-get install -y libps2000a

# Reload udev rules so the USB device is accessible without sudo
sudo udevadm control --reload-rules && sudo udevadm trigger

# Add the service user to the pico group (then reboot or re-login)
sudo usermod -aG pico $USER

# Install the Python wrapper and numpy (if not already installed)
pip3 install picosdk numpy
```

> **Note:** if `ps2000aOpenUnit` returns error 282, the 2204A is on a USB 2.0
> port instead of USB 3.0 — it still works fine, this is just a warning.

### PicoScope POST payload

Each POST includes a `picoscope_ch_a` object alongside the other sensor fields:

```json
{
  "picoscope_ch_a": {
    "n_samples":    5000,
    "median_mv":    1.2340,
    "mean_mv":      1.2501,
    "trim_mean_mv": 1.2480,
    "trim_min_mv":  0.9120,
    "trim_max_mv":  1.5870,
    "samples_b64z": "<zlib-compressed float32 LE waveform, base64-encoded>"
  }
}
```

`samples_b64z` is a zlib-compressed, base64-encoded `float32` (little-endian)
array of instantaneous mV values. To decompress:

```python
import base64, zlib, numpy as np
buf = base64.b64decode(payload["picoscope_ch_a"]["samples_b64z"])
mv  = np.frombuffer(zlib.decompress(buf), dtype=np.float32)
```

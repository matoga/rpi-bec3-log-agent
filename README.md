# BEC3 RPi Environment Logger

Continuously reads temperature, humidity, and light level and posts the data
to a remote server every 60 seconds. Status is shown live on the LCD display.

---

## Hardware

| Device | Grove port | Notes |
|---|---|---|
| DHT22 temp/humidity | D5 | digital |
| Grove Light Sensor | A0 | analog |
| Grove LCD RGB Backlight | I2C | address 0x3e / 0x30 |
| Push button | D22 | hardware pull-down |

---

## LCD display

```
+----------------+
| 08:30:01 OK   / |   ← line 1: time · status · indicator
| H24 T26.3 L34   |   ← line 2: sensor readings
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
pip3 install requests smbus2

# 4. Configure
cp config/device.toml.example config/device.toml
nano config/device.toml   # set server_url and api_key

# 5. Run manually to verify
python3 agent.py

# 6. Install as a service
bash install-service.sh
```

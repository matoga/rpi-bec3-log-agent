# BEC3 RPi Log Agent

Reads temperature, humidity (DHT22), and light level (Grove Light Sensor) and posts
the data to a remote HTTP endpoint on a fixed interval. Status is shown on a Grove LCD RGB Backlight display.

## Requirements

- Raspberry Pi with Grove Base Hat
- Python 3.11+
- [grove.py](https://github.com/Seeed-Studio/grove.py) library

## Setup

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
```

## Run

```bash
python3 agent.py
```

Logs go to stdout. Stop with Ctrl-C.

## Run as a service (optional)

```bash
bash install-service.sh
```

The script auto-detects the repo path and current user, writes the systemd unit
file, and starts the service immediately.

```bash
# Check status
sudo systemctl status rpi-bec3-log-agent

# Follow logs
journalctl -u rpi-bec3-log-agent -f
```

## Stop the service

```bash
bash stop-service.sh
```

Stops the agent and disables it from starting on boot. Run `install-service.sh` again to re-enable.

## LCD display

Line 1 — send status + spinning liveness indicator (cycles `/` `-` `\` `|`):

```
08:30:01 OK      /    ← all sensors OK, data sent
08:30:01 OK      -    ← same, next tick (one sensor missing → yellow backlight)
08:30:01 F:404   \    ← server returned HTTP 404
08:30:01 F:CON   |    ← no network / connection refused
08:30:01 F:TMO   /    ← server did not respond within 10 s
08:30:01 F:ND    -    ← no sensor returned data at all
08:30:01 F:ERR   \    ← unexpected error
```

Line 2 — sensor readings (only present sensors shown):

```
H24 T26.3 L34         ← humidity %, temperature °C, light 0–99
T26.3 L34             ← DHT22 failed this cycle
L34                   ← only light sensor working
no data               ← nothing working
```

Backlight colours: **white** = all OK · **yellow** = at least one sensor missing · **red** = send failed

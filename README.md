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
sudo cp rpi-bec3-log-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpi-bec3-log-agent

# Logs
journalctl -u rpi-bec3-log-agent -f
```

The service file assumes the repo is cloned to `/home/admin/rpi-bec3-log-agent`
and runs as user `admin`. Edit the service file if your setup differs.

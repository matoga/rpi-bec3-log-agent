#!/usr/bin/env bash
set -e

SERVICE=rpi-bec3-log-agent
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="$(whoami)"
PYTHON="$(which python3)"

echo "Installing ${SERVICE} as a systemd service..."
echo "  Repo:   ${REPO_DIR}"
echo "  User:   ${RUN_USER}"
echo "  Python: ${PYTHON}"

sudo tee /etc/systemd/system/${SERVICE}.service > /dev/null <<EOF
[Unit]
Description=BEC3 RPi Log Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${PYTHON} ${REPO_DIR}/agent.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ${SERVICE}

echo ""
echo "Done. Service is running."
echo "  Status:  sudo systemctl status ${SERVICE}"
echo "  Logs:    journalctl -u ${SERVICE} -f"
echo "  Stop:    ./stop-service.sh"

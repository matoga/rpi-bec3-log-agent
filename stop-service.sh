#!/usr/bin/env bash
set -e

SERVICE=rpi-bec3-log-agent

sudo systemctl stop ${SERVICE}
sudo systemctl disable ${SERVICE}

echo "Service ${SERVICE} stopped and disabled."
echo "To restart it later:  ./install-service.sh"

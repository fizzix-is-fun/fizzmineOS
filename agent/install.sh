#!/bin/bash
# Installs the agent on an Ubuntu rig. Run from the repo: sudo ./agent/install.sh
set -e
cd "$(dirname "$0")"
sudo mkdir -p /opt/fizzmine /etc/fizzmine
sudo rm -rf /opt/fizzmine/agent && sudo cp -r . /opt/fizzmine/agent
[ -d /opt/fizzmine/venv ] || sudo python3 -m venv /opt/fizzmine/venv
sudo /opt/fizzmine/venv/bin/pip install -q -r requirements.txt
[ -f /etc/fizzmine/agent.toml ] || sudo cp agent.example.toml /etc/fizzmine/agent.toml
sudo cp fizzagent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fizzagent
echo "Agent installed. Edit /etc/fizzmine/agent.toml (rig, hub_url, token), then: sudo systemctl restart fizzagent"

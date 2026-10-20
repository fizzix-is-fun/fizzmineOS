# fizzmineOS

Home GPU rig control center: a small agent on each rig reports GPU and miner stats to one hub, which serves a web dashboard.

Phase 1 is read-only: it watches temps, fans, power, clocks, VRAM and WildRig hashrate. Nothing changes clocks or fans yet.

```
agent/   rig service (Python, NVML), installer and systemd unit
hub/     control center (FastAPI + SQLite) and the dashboard, runs in Docker
tests/   pytest suite, runs without a GPU
```

## Run the hub

On the box that will host it (any machine on the LAN):

```bash
cd hub
mkdir -p config && cp hub.example.toml config/hub.toml   # add one token per rig: openssl rand -hex 16
docker compose up -d --build
```

Dashboard: `http://<hub-ip>:8780`

## Install the agent on a rig

```bash
git clone https://github.com/fizzix-is-fun/fizzmineOS && cd fizzmineOS
sudo ./agent/install.sh
sudo nano /etc/fizzmine/agent.toml     # rig name, hub_url = "ws://<hub-ip>:8780", token
sudo systemctl restart fizzagent
journalctl -u fizzagent -f
```

For hashrate, add `--api-port 60050` to the wildrig command in `/usr/local/bin/mine`. When the miner isn't running the rig shows as "AI / idle".

## Try it without a GPU

```bash
pip install fastapi uvicorn websockets nvidia-ml-py pytest
python -m pytest tests
(cd hub && FIZZHUB_CONFIG=hub.example.toml FIZZHUB_DB=/tmp/hub.db uvicorn fizzhub.app:app --port 8780) &
# agent.toml with rig = "babyarm", hub_url = "ws://127.0.0.1:8780", token = "change-me"
(cd agent && python -m fizzagent --config /path/to/agent.toml --fake)
```

## Roadmap

1. Watch (this): live stats and 7 days of history
2. Control: mine/AI toggle, power, clock locks, offsets and fan targets per card
3. Flight sheets: saved miner + pool + wallet + tuning, pushed to rigs
4. Safety and alerts: watchdog, fallback to stock clocks, notifications
5. Automation: pause mining while the AI is in use

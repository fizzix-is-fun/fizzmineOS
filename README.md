# fizzmineOS

Home GPU rig control center: a small agent on each rig reports GPU and miner stats to one hub, which serves a web dashboard.

Phase 1 is read-only: it watches temps, fans, power, clocks, VRAM and WildRig hashrate. Nothing changes clocks or fans yet.

```
agent/   rig service (Python, NVML), rig installer and systemd unit
hub/     control center (FastAPI + SQLite), the dashboard and the hub setup script; runs in Docker
docs/    install guide
tests/   pytest suite, runs without a GPU
```

## Install

Step-by-step guide: [docs/install-guide.md](docs/install-guide.md). Short version:

```bash
# on the hub (mini PC, Ubuntu Server 24.04)
curl -fsSL https://raw.githubusercontent.com/fizzix-is-fun/fizzmineOS/main/hub/setup-hub.sh | sudo bash

# on each rig: the hub prints this command with its address and enroll key (sudo fizzhub-key)
curl -fsSL http://<hub-ip>:8780/install.sh | sudo bash -s -- --key <enroll-key>
```

The rig installer sets up Tailscale (`--tailscale-key`), the NVIDIA driver (`--driver recommended|580|keep|skip`) and the agent, then enrolls the rig with the hub. Rigs only run the agent (about 30 MB of RAM) and the miner, so 2011-era dual-core mining boards with 4 GB are fine.

## Try it without a GPU

```bash
pip install fastapi uvicorn websockets nvidia-ml-py pytest
python -m pytest tests
(cd hub && FIZZHUB_CONFIG=hub.example.toml FIZZHUB_DB=/tmp/hub.db uvicorn fizzhub.app:app --port 8780) &
# enroll a test rig: curl -X POST localhost:8780/api/enroll -H "Content-Type: application/json" -d '{"key":"change-me","rig":"test"}'
# then write an agent.toml with rig = "test", hub_url = "ws://127.0.0.1:8780" and the returned token
(cd agent && python -m fizzagent --config /path/to/agent.toml --fake)
```

## Roadmap

1. Watch: live stats and 7 days of history
2. One-command setup for the hub and rigs
3. Control: mine/AI toggle, power, clock locks, offsets and fan targets per card
4. Flight sheets: saved miner + pool + wallet + tuning, pushed to rigs
5. Safety and alerts: watchdog, fallback to stock clocks, notifications
6. Automation: pause mining while the AI is in use

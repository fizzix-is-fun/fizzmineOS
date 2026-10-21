# fizzmineOS

Home GPU rig control center: a small agent on each rig reports GPU and miner stats to one hub, which serves a web dashboard.

It watches temps, fans, power, clocks, offsets and hashrate per card, and lets you run rigs from the browser: flight sheets pushed to chosen rigs, per-card overclocks and fan control, OC profiles by card model, miner start/stop/restart, reboot, and a mine/AI switch. See [docs/controls.md](docs/controls.md).

```
agent/   rig service (Python, NVML), rig installer and systemd unit
hub/     control center (FastAPI + SQLite), the dashboard and the hub setup script; runs in Docker
docs/    install guide and how to use the controls
tests/   pytest suite, runs without a GPU
```

## Install

Step-by-step guide: [docs/install-guide.md](docs/install-guide.md). Short version:

```bash
# on the hub (mini PC, Ubuntu Server 26.04)
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
3. Control: login, mine/AI switch, miner and rig controls, per-card OC and fans, OC profiles by card model
4. Flight sheets: miner download, pool, wallet and arguments, pushed to chosen rigs
5. Safety and alerts: temperature protection, hashrate watchdog, Discord status and alert channels
6. Miner console in the browser, driver picking from the dashboard
7. Automation: pause mining while the AI is in use

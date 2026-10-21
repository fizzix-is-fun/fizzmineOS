"""fizzmine agent: reports this rig's GPU and miner stats to the hub and carries out its commands.

usage: python -m fizzagent [--config /etc/fizzmine/agent.toml] [--state /etc/fizzmine/state.json] [--fake]
"""
import argparse
import asyncio
import json
import logging
import socket
import subprocess
import time
import tomllib
from pathlib import Path

import websockets

from .fans import FanController
from .gpu import FakeGpus, NvmlGpus
from .miner import FakeMiner, WildRig, attach_to_gpus
from .runner import MinerRunner, read_log

log = logging.getLogger("fizzagent")

OC_FIELDS = ("power_w", "core_lock_mhz", "mem_lock_mhz", "core_offset_mhz", "mem_offset_mhz", "fan_mode", "fan_value")


def clean_oc(oc):
    out = {}
    for k in OC_FIELDS:
        v = (oc or {}).get(k)
        if v in (None, ""):
            continue
        out[k] = v if k == "fan_mode" else int(v)
    if out.get("fan_mode") not in (None, "auto", "fixed", "target"):
        raise ValueError("fan_mode must be auto, fixed or target")
    return out


class Agent:
    """Everything the rig does, kept in a small state file so a reboot comes back the same way:
    mode ("mining" / "ai" / "stopped"), the flight sheet, per-card OC by GPU UUID, and AI-mode commands."""

    def __init__(self, cfg, fake=False, state_path="/etc/fizzmine/state.json"):
        self.rig = cfg.get("rig") or socket.gethostname()
        self.state_path = Path(state_path)
        self.gpus = FakeGpus() if fake else NvmlGpus()
        self.miner = FakeMiner() if fake else WildRig(cfg.get("wildrig_api_port", 60050))
        self.runner = MinerRunner(self.rig, fake=fake)
        self.fans = FanController(self.gpus)
        self.fake = fake
        self.log_path = None  # default: runner.LOG_FILE
        self.state = {"mode": "stopped", "flight_sheet": None, "oc": {}, "ai": {"start_cmd": "", "stop_cmd": ""}}
        try:
            self.state.update(json.loads(self.state_path.read_text()))
        except (OSError, ValueError):
            pass

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.state_path)

    def cards(self):
        """card index -> uuid, in NVML order"""
        return {g["index"]: g["uuid"] for g in self.gpus.read()}

    def card_oc(self):
        """card index -> saved OC, for the cards that have one"""
        return {i: self.state["oc"][u] for i, u in self.cards().items() if u in self.state["oc"]}

    def apply_saved_oc(self):
        errors = []
        for i, oc in self.card_oc().items():
            errors += [f"GPU {i} {e}" for e in self.gpus.apply_oc(i, oc)]
        return errors

    def reset_oc(self):
        self.fans.release_all()
        for i in self.card_oc():
            self.gpus.apply_oc(i, {})

    def snapshot(self):
        cards, stats = self.gpus.read(), self.miner.read()
        if self.fake and not self.runner.running():
            stats = {"running": False, "miner": "wildrig"}
        attach_to_gpus(cards, stats)
        for g in cards:
            g["oc"] = self.state["oc"].get(g["uuid"])
        fs = self.state["flight_sheet"]
        return {
            "type": "stats", "rig": self.rig, "ts": time.time(), "gpus": cards, "miner": stats,
            "agent": {"mode": self.state["mode"], "flight_sheet": fs.get("name") if fs else None,
                      "miner_running": self.runner.running(), "ai": self.state["ai"]},
        }

    def run_hook(self, which):
        cmd = self.state["ai"].get(which)
        if cmd and not self.fake:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
            if r.returncode:
                return f"{which} failed: {(r.stderr or r.stdout).strip()[-300:]}"
        return None

    # --- the things the dashboard can ask for

    def start_mining(self):
        fs = self.state["flight_sheet"]
        if not fs:
            raise ValueError("no flight sheet on this rig yet")
        notes = [n for n in [self.run_hook("stop_cmd")] if n]
        notes += self.apply_saved_oc()
        self.state["mode"] = "mining"
        self.save()
        return "; ".join([self.runner.start(fs)] + notes)

    def stop_mining(self, mode="stopped"):
        msg = self.runner.stop()
        self.reset_oc()
        self.state["mode"] = mode
        self.save()
        return msg

    def handle(self, cmd, args):
        if cmd == "set_oc":
            for uuid, oc in (args.get("cards") or {}).items():
                if oc is None:
                    self.state["oc"].pop(uuid, None)
                else:
                    self.state["oc"][uuid] = clean_oc(oc)
            self.save()
            if self.state["mode"] != "mining":
                return "saved; applies when mining starts"
            # cards whose OC was cleared go back to stock
            for i, u in self.cards().items():
                if u in (args.get("cards") or {}) and args["cards"][u] is None:
                    self.gpus.apply_oc(i, {})
                    self.fans.tick({i: {"fan_mode": "auto"}})
            errors = self.apply_saved_oc()
            return "applied" + (": " + "; ".join(errors) if errors else "")
        if cmd == "flight_sheet":
            fs = args["flight_sheet"]
            self.state["flight_sheet"] = fs
            self.save()
            if self.runner.running():
                self.runner.stop()
                return "restarted with " + fs.get("name", "new flight sheet") + ": " + self.runner.start(fs)
            if args.get("start"):
                return self.start_mining()
            return "saved"
        if cmd == "miner_start":
            return self.start_mining()
        if cmd == "miner_stop":
            return self.stop_mining()
        if cmd == "miner_restart":
            self.runner.stop()
            return self.start_mining()
        if cmd == "set_mode":
            if args.get("mode") == "mining":
                return self.start_mining()
            if args.get("mode") == "ai":
                msg = self.stop_mining(mode="ai")
                note = self.run_hook("start_cmd")
                return "AI mode: " + msg + (f"; {note}" if note else "")
            raise ValueError("mode must be mining or ai")
        if cmd == "set_ai":
            self.state["ai"] = {"start_cmd": args.get("start_cmd", ""), "stop_cmd": args.get("stop_cmd", "")}
            self.save()
            return "saved"
        if cmd == "miner_log":
            out = read_log(args.get("offset"), path=self.log_path)
            out["running"] = self.runner.running()
            return out
        if cmd == "miner_screen":
            return {"screen": self.runner.screen(), "running": self.runner.running()}
        if cmd == "miner_keys":
            return self.runner.send_keys(list(args.get("keys") or []))
        if cmd == "reboot":
            if not self.fake:
                subprocess.Popen(["bash", "-c", "sleep 2; systemctl reboot"])
            return "rebooting"
        raise ValueError(f"unknown command {cmd}")

    def boot(self):
        """On agent start: pick up where the rig left off."""
        if self.state["mode"] == "mining" and self.state["flight_sheet"]:
            try:
                log.info("resuming mining: %s", self.start_mining())
            except Exception as e:
                log.error("couldn't resume mining: %s", e)

    def tick(self):
        if self.state["mode"] == "mining":
            self.fans.tick(self.card_oc())


async def run(cfg, agent):
    url = f"{cfg['hub_url'].rstrip('/')}/ws/agent?rig={agent.rig}&token={cfg['token']}"
    interval = cfg.get("interval_s", 5)
    await asyncio.to_thread(agent.boot)

    async def send_stats(ws):
        while True:
            agent.tick()
            await ws.send(json.dumps(await asyncio.to_thread(agent.snapshot)))
            await asyncio.sleep(interval)

    busy = asyncio.Lock()  # one rig-changing command at a time

    async def do_command(ws, msg):
        quiet = msg.get("cmd") in ("miner_log", "miner_screen", "miner_keys")  # the console, every second or so
        if not quiet:
            log.info("command %s %s", msg.get("cmd"), msg.get("args"))
        try:
            if quiet:
                out = agent.handle(msg["cmd"], msg.get("args") or {})
            else:
                async with busy:
                    out = await asyncio.to_thread(agent.handle, msg["cmd"], msg.get("args") or {})
            result = {"ok": True, "msg": out}
        except Exception as e:
            log.exception("command %s failed", msg.get("cmd"))
            result = {"ok": False, "msg": str(e)}
        await ws.send(json.dumps({"type": "result", "id": msg.get("id"), **result}))
        if not quiet:
            # report the new state right away instead of waiting for the next tick
            await ws.send(json.dumps(await asyncio.to_thread(agent.snapshot)))

    async def take_commands(ws):
        # each command in its own task, so the console keeps updating while a long one (a miner download) runs
        running = set()
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") != "cmd":
                continue
            t = asyncio.create_task(do_command(ws, msg))
            running.add(t)
            t.add_done_callback(running.discard)

    # websockets' connect() as an async iterator reconnects with backoff when the hub goes away
    async for ws in websockets.connect(url, open_timeout=10):
        log.info("connected to hub as %s", agent.rig)
        tasks = [asyncio.create_task(send_stats(ws)), asyncio.create_task(take_commands(ws))]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()
        except websockets.ConnectionClosed as e:
            log.warning("hub connection closed (%s), reconnecting", e)
        finally:
            for t in tasks:
                t.cancel()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="/etc/fizzmine/agent.toml")
    p.add_argument("--state", default="/etc/fizzmine/state.json")
    p.add_argument("--fake", action="store_true", help="pretend GPUs and miner, for testing")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    asyncio.run(run(cfg, Agent(cfg, fake=args.fake, state_path=args.state)))


if __name__ == "__main__":
    main()

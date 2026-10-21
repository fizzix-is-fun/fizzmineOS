"""fizz: control this rig from SSH, through the agent, when the dashboard isn't handy (or the hub is down).

The agent keeps everything on the rig, so the dashboard shows whatever you set here once the hub is back.
"""
import argparse
import json
import os
import socket
import sys

from .__main__ import OC_FIELDS, SOCKET
from .runner import LOG_FILE, SESSION

HELP = """examples:
  fizz status                      what the rig and each card are doing
  fizz stop | start | restart      the miner
  fizz ai                          switch to AI mode (stops the miner, cards to stock)
  fizz oc                          show each card's OC
  fizz oc all --pl 140 --core-lock 2685 --core-offset 350 --mem-lock 810
  fizz oc 1 --core-offset 300      change one setting on GPU 1, keep the rest
  fizz oc 0 --fan 70               fan: auto, a fixed % like 70, or a target like t65
  fizz oc 0 --reset                GPU 0 back to stock
  fizz screen                      the miner's screen (Ctrl+B then D to leave it running)
  fizz log                         follow the miner log (Ctrl+C to stop following)
"""
OPTS = {"pl": "power_w", "core_lock": "core_lock_mhz", "core_offset": "core_offset_mhz",
        "mem_lock": "mem_lock_mhz", "mem_offset": "mem_offset_mhz"}


def ask(cmd, args=None, path=SOCKET):
    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect(path)
    except PermissionError:
        sys.exit("run it with sudo: sudo fizz ...")
    except OSError:
        sys.exit("the agent isn't running (sudo systemctl status fizzagent)")
    s.settimeout(200)
    s.sendall((json.dumps({"cmd": cmd, "args": args or {}}) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    res = json.loads(data)
    if not res["ok"]:
        sys.exit("error: " + res["msg"])
    return res["msg"]


def fan_setting(v):
    v = v.strip().lower().rstrip("%")
    if v == "auto":
        return {"fan_mode": "auto"}
    if v.startswith("t"):
        return {"fan_mode": "target", "fan_value": int(v[1:])}
    return {"fan_mode": "fixed", "fan_value": int(v)}


def oc_line(oc):
    if not oc:
        return "stock"
    names = {"power_w": "PL {} W", "core_lock_mhz": "core lock {}", "core_offset_mhz": "core {:+d}",
             "mem_lock_mhz": "mem lock {}", "mem_offset_mhz": "mem {:+d}"}
    parts = [names[k].format(oc[k]) for k in names if oc.get(k) is not None]
    if oc.get("fan_mode") in ("fixed", "target"):
        parts.append(f"fan {oc['fan_value']}%" if oc["fan_mode"] == "fixed" else f"fan target {oc['fan_value']} °C")
    return ", ".join(parts) or "stock"


def show_status(snap):
    a, m = snap["agent"], snap["miner"]
    rate = f"{m['hashrate_hs'] / 1e12:.1f} TH/s" if m.get("running") and m.get("hashrate_hs") is not None else "–"
    power = sum(g.get("power_w") or 0 for g in snap["gpus"])
    print(f"{snap['rig']}: {a['mode']}, miner {'running' if a['miner_running'] else 'stopped'}, {rate}, {power:.0f} W"
          + (f", flight sheet {a['flight_sheet']}" if a.get("flight_sheet") else ""))
    if m.get("shares_accepted") is not None:
        print(f"  shares {m['shares_accepted']} accepted / {m.get('shares_rejected') or 0} rejected")
    wd = a.get("watchdog") or {}
    print(f"  watchdog: {'off' if not wd.get('enabled', True) else wd.get('status', 'ok')}")
    for g in snap["gpus"]:
        hr = f"{g['hashrate_hs'] / 1e12:.1f} TH/s" if g.get("hashrate_hs") is not None else "–"
        print(f"  GPU {g['index']} {g.get('brand') or ''} {g['name'].replace('NVIDIA GeForce ', '')}: "
              f"{g.get('temp_c')} °C, fan {g.get('fan_pct')}%, {g.get('power_w')} W, core {g.get('core_mhz')} MHz, {hr}")
        print(f"      OC: {oc_line(g.get('oc'))}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="fizz", description=__doc__.splitlines()[0], epilog=HELP,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--socket", default=SOCKET, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("status", "start", "stop", "restart", "ai", "screen", "log"):
        sub.add_parser(name)
    oc = sub.add_parser("oc", help="show or change card OC")
    oc.add_argument("gpu", nargs="?", help="GPU number, or all")
    for o in OPTS:
        oc.add_argument("--" + o.replace("_", "-"), type=int)
    oc.add_argument("--fan", help="auto, a % like 70, or a target temperature like t65")
    oc.add_argument("--reset", action="store_true", help="back to stock")
    a = p.parse_args(argv)
    path = a.socket

    if a.cmd == "screen":
        os.execvp("tmux", ["tmux", "attach", "-t", SESSION])
    if a.cmd == "log":
        os.execvp("tail", ["tail", "-n", "100", "-F", str(LOG_FILE)])
    if a.cmd == "status":
        return show_status(ask("status", path=path))
    if a.cmd in ("start", "stop", "restart", "ai"):
        cmd = {"start": ("set_mode", {"mode": "mining"}), "stop": ("miner_stop", {}),
               "restart": ("miner_restart", {}), "ai": ("set_mode", {"mode": "ai"})}[a.cmd]
        return print(ask(*cmd, path=path))

    # oc
    snap = ask("status", path=path)
    changes = {OPTS[o]: getattr(a, o) for o in OPTS if getattr(a, o) is not None}
    if a.fan:
        changes.update(fan_setting(a.fan))
    if a.gpu is None or (not changes and not a.reset):
        for g in snap["gpus"]:
            print(f"GPU {g['index']} {g['name'].replace('NVIDIA GeForce ', '')}: {oc_line(g.get('oc'))}")
        return
    picked = snap["gpus"] if a.gpu == "all" else [g for g in snap["gpus"] if str(g["index"]) == a.gpu]
    if not picked:
        sys.exit(f"no GPU {a.gpu}; this rig has {', '.join(str(g['index']) for g in snap['gpus'])}")
    cards = {}
    for g in picked:
        if a.reset:
            cards[g["uuid"]] = None
            continue
        new = {k: v for k, v in (g.get("oc") or {}).items() if k in OC_FIELDS}
        new.update(changes)
        cards[g["uuid"]] = new or None
    print(ask("set_oc", {"cards": cards}, path=path))


if __name__ == "__main__":
    main()

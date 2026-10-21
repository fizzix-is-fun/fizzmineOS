"""Runs the miner from a flight sheet inside a tmux session named "miner" (attach: sudo tmux attach -t miner)."""
import logging
import os
import shlex
import shutil
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("fizzagent")

MINERS_DIR = Path("/opt/fizzmine/miners")
LOG_FILE = Path("/var/log/fizzmine/miner.log")
SESSION = "miner"
SCREEN = (120, 40)  # columns, rows of the miner's terminal
# keys the console may send besides plain characters (Ctrl+C is left to the Stop miner button)
KEY_NAMES = {"Enter", "Escape", "Tab", "BSpace", "Up", "Down", "Left", "Right", "PageUp", "PageDown", "Home", "End", "Space"}
LOG_KEEP = 20_000_000  # bytes; on start a bigger log moves to miner.log.1

# Built-in miners: which file to run and how a flight sheet becomes its arguments.
MINERS = {
    "wildrig-ng": {
        "binaries": ["wildrig-multi", "wildrig-ng", "wildrig"],
        "args": "--algo {algo} --url {pool} --user {wallet} --worker {worker} --api-port {api_port}",
    },
    "custom": {"binaries": [], "args": ""},
}


def fill(text, rig):
    """HiveOS-style placeholders in wallets and worker names."""
    return (text or "").replace("%RIG%", rig).replace("%WORKER_NAME%", rig)


def build_args(fs, rig):
    miner = MINERS.get(fs.get("miner"), MINERS["custom"])
    values = {
        "algo": fs.get("algo", ""), "pool": fs.get("pool", ""), "wallet": fill(fs.get("wallet"), rig),
        "worker": fill(fs.get("worker") or "%RIG%", rig), "api_port": fs.get("api_port") or 60050,
    }
    template = fs.get("custom_args") or miner["args"]
    args = [part.format(**{k: str(v) for k, v in values.items()}) for part in shlex.split(template)]
    return args + shlex.split(fill(fs.get("extra_args"), rig))


def find_binary(root, names):
    for name in names:
        for path in Path(root).rglob(name):
            if path.is_file():
                return path
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def fetch_miner(url):
    """Downloads and unpacks a miner archive once; later flight sheets with the same URL reuse it."""
    name = Path(url.split("?")[0]).name
    for ext in (".tar.gz", ".tar.xz", ".tgz", ".txz", ".zip", ".tar"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    dest = MINERS_DIR / name
    if not dest.exists():
        MINERS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        archive = tmp / "download"
        log.info("downloading miner %s", url)
        with urllib.request.urlopen(url, timeout=120) as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:
            with tarfile.open(archive) as t:
                t.extractall(tmp, filter="data")
        archive.unlink()
        for path in tmp.rglob("*"):  # zip and some tarballs lose the executable bit
            if path.is_file() and not path.suffix:
                path.chmod(path.stat().st_mode | 0o755)
        tmp.rename(dest)
    return dest


class MinerRunner:
    def __init__(self, rig, fake=False):
        self.rig, self.fake, self._fake_running = rig, fake, False

    def resolve(self, fs):
        names = MINERS.get(fs.get("miner"), MINERS["custom"])["binaries"]
        if fs.get("miner_binary"):
            names = [fs["miner_binary"]]
        if fs.get("miner_path"):
            path = Path(fs["miner_path"])
            return path if path.is_file() else find_binary(path, names)
        if fs.get("miner_url"):
            return find_binary(fetch_miner(fs["miner_url"]), names)
        raise ValueError("flight sheet needs a miner download URL or a path on the rig")

    def command(self, fs):
        binary = self.resolve(fs)
        if not binary:
            raise ValueError("couldn't find the miner program in the download")
        return binary, [str(binary)] + build_args(fs, self.rig)

    def running(self):
        if self.fake:
            return self._fake_running
        return subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True).returncode == 0

    def start(self, fs):
        if self.running():
            return "miner already running"
        if self.fake:
            self._fake_running = True
            return "started (fake)"
        binary, cmd = self.command(fs)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_KEEP:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        # the miner gets a real terminal (so it draws its own screen, as on HiveOS); tmux copies what it prints
        # to the log. The short sleep lets pipe-pane attach before the miner's first line.
        line = f"sleep 0.5; cd {shlex.quote(str(binary.parent))} && exec {shlex.join(cmd)}"
        cols, rows = SCREEN
        subprocess.run(["tmux", "new-session", "-d", "-s", SESSION, "-x", str(cols), "-y", str(rows), "bash", "-c", line], check=True)
        subprocess.run(["tmux", "pipe-pane", "-o", "-t", SESSION, f"cat >> {shlex.quote(str(LOG_FILE))}"], check=True)
        log.info("miner started: %s", shlex.join(cmd))
        return "started"

    def screen(self):
        """What the miner's terminal shows right now, with its colours; None when it isn't running."""
        if self.fake:
            return FAKE_SCREEN if self._fake_running else None
        r = subprocess.run(["tmux", "capture-pane", "-p", "-e", "-t", SESSION], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def send_keys(self, keys):
        for k in keys:
            if not (k in KEY_NAMES or (len(k) == 1 and k.isprintable())):
                raise ValueError(f"key not allowed: {k!r}")
        if self.fake or not self.running():
            return "sent" if self.fake else "miner not running"
        for k in keys:
            subprocess.run(["tmux", "send-keys", "-t", SESSION] + (["-l", k] if k not in KEY_NAMES else [k]), check=True)
        return "sent"

    def stop(self):
        if self.fake:
            self._fake_running = False
            return "stopped (fake)"
        if not self.running():
            return "miner not running"
        subprocess.run(["tmux", "send-keys", "-t", SESSION, "C-c"], capture_output=True)
        for _ in range(20):
            if not self.running():
                return "stopped"
            time.sleep(0.5)
        subprocess.run(["tmux", "kill-session", "-t", SESSION], capture_output=True)
        return "stopped (killed)"


def read_log(offset=None, path=None, tail=64_000, chunk=256_000):
    """New miner output since `offset` (bytes). No offset, or a log that was rotated: the last `tail` bytes."""
    path = Path(path or LOG_FILE)
    try:
        size = path.stat().st_size
    except OSError:
        return {"text": "", "offset": 0}
    if offset is None or offset > size:
        offset = max(0, size - tail)
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(chunk)
    return {"text": data.decode("utf-8", "replace"), "offset": offset + len(data)}


FAKE_SCREEN = """\x1b[1;36m WildRig 1.0.0l\x1b[0m  pearlhash  pool.pearlhash.xyz:9000
\x1b[7m GPU   Hashrate     Temp  Fan   Power  A/R    \x1b[0m
 #0    \x1b[32m93.7 TH/s\x1b[0m    64C   42%   112W   41/0
 #1    \x1b[32m93.6 TH/s\x1b[0m    59C   36%   107W   38/0
 Total \x1b[1;32m187.3 TH/s\x1b[0m                   219W   79/0

\x1b[33m[share] accepted\x1b[0m 38 ms   \x1b[90mpress h for help\x1b[0m
"""

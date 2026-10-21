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
        line = f"cd {shlex.quote(str(binary.parent))} && {shlex.join(cmd)} 2>&1 | tee -a {LOG_FILE}"
        subprocess.run(["tmux", "new-session", "-d", "-s", SESSION, "-x", "200", "-y", "50", "bash", "-c", line], check=True)
        log.info("miner started: %s", shlex.join(cmd))
        return "started"

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

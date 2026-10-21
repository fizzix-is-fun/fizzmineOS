#!/bin/bash
# fizzmineOS rig installer. The hub serves this file with its own address filled in, so on a
# fresh Ubuntu Server 26.04 rig you only run:
#
#   curl -fsSL http://<hub>:8780/install.sh | sudo bash -s -- --key <enroll key>
#
# Options:
#   --key KEY              enroll key from the hub (required)
#   --name NAME            rig name on the dashboard (default: hostname)
#   --driver VER           NVIDIA driver: "recommended", a version like 580, "keep" or "skip"
#                          (default: recommended when no driver is installed, keep otherwise)
#   --tailscale-key KEY    join your tailnet with this auth key
#   --no-reboot            don't reboot after a driver install
# Running it again is safe: it updates the agent and re-enrolls the rig.
set -euo pipefail

HUB="__HUB_URL__"
KEY="" NAME="$(hostname)" DRIVER="" TS_KEY="" REBOOT=1 NEED_REBOOT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --driver) DRIVER="$2"; shift 2 ;;
    --tailscale-key) TS_KEY="$2"; shift 2 ;;
    --no-reboot) REBOOT=0; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"
[ -n "$KEY" ] || die "missing --key (find it on the hub: sudo fizzhub-key)"
[[ "$NAME" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || die "rig name '$NAME' must be letters, digits, - or _ (use --name)"
OS_ID=$(. /etc/os-release; echo "$ID")  # in a subshell: os-release sets NAME, which would clobber ours
[ "$OS_ID" = ubuntu ] || die "this installer is for Ubuntu (found $OS_ID)"

# A wrong clock (common on fresh installs and old boards with dead CMOS batteries)
# makes apt reject the package lists as "not valid yet".
sync_clock() {
  timedatectl set-ntp true 2>/dev/null || true
  systemctl restart systemd-timesyncd 2>/dev/null || true
  for _ in $(seq 1 15); do
    [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && return
    sleep 2
  done
  local web
  web=$(curl -fsSI --max-time 10 https://www.google.com 2>/dev/null | grep -i '^date:' | cut -d' ' -f2- | tr -d '\r' || true)
  if [ -n "$web" ] && date -s "$web" >/dev/null; then echo "  clock set from the web: $(date)"; fi
}

say "Checking the clock"
sync_clock

say "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
# one stale mirror (e.g. "not valid yet") shouldn't stop the install; the packages come from the others
apt-get update -q || echo "  (apt update reported a problem above; continuing)"
apt-get install -y -q python3 python3-venv tmux curl pciutils ubuntu-drivers-common >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "needs Python 3.11+ (Ubuntu 24.04 or newer)"

say "CPU check"
FLAGS=$(grep -m1 '^flags' /proc/cpuinfo)
for f in sse4_2 aes avx avx2; do
  if grep -qw "$f" <<<"$FLAGS"; then echo "  $f: yes"; else echo "  $f: no"; fi
done
echo "  $(grep -c ^processor /proc/cpuinfo) threads, $(awk '/MemTotal/{printf "%.1f", $2/1048576}' /proc/meminfo) GB RAM"

if [ -n "$TS_KEY" ]; then
  say "Joining Tailscale"
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  tailscale up --authkey="$TS_KEY" --hostname="$NAME" --ssh
elif ! command -v tailscale >/dev/null; then
  echo "  (skipping Tailscale: add --tailscale-key to set it up)"
fi

if lspci -nn | grep -i '\[10de:' >/dev/null; then
  CURRENT=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)
  [ -n "$DRIVER" ] || { [ -n "$CURRENT" ] && DRIVER=keep || DRIVER=recommended; }
  say "NVIDIA driver (installed: ${CURRENT:-none}, requested: $DRIVER)"
  RECOMMENDED=$(ubuntu-drivers devices 2>/dev/null | awk '/recommended/{print $3}' | head -1 || true)
  case "$DRIVER" in
    keep|skip) PKG="" ;;
    recommended) PKG="$RECOMMENDED"; [ -n "$PKG" ] || echo "  ubuntu-drivers found no recommended driver; try --driver 580" ;;
    [0-9]*)
      # RTX 20-series and newer use the open kernel modules (required for 50-series)
      if [[ "$RECOMMENDED" == *-open ]]; then PKG="nvidia-driver-$DRIVER-open"; else PKG="nvidia-driver-$DRIVER"; fi ;;
    *) die "--driver must be recommended, keep, skip or a version number" ;;
  esac
  if [ -n "$PKG" ] && [ "${CURRENT%%.*}" != "$(grep -o '[0-9]\+' <<<"$PKG" | head -1)" ]; then
    echo "  installing $PKG"
    apt-get install -y -q "$PKG" >/dev/null || die "driver install failed; see: ubuntu-drivers list"
    NEED_REBOOT=1
  else
    echo "  no driver change"
  fi
  systemctl enable --now nvidia-persistenced >/dev/null 2>&1 || true
else
  echo "  no NVIDIA GPU found, skipping driver"
fi

say "Installing the fizzmine agent"
mkdir -p /opt/fizzmine /etc/fizzmine
TMP=$(mktemp -d)
curl -fsSL "$HUB/agent.tar.gz" | tar -xz -C "$TMP"
rm -rf /opt/fizzmine/agent && mv "$TMP/agent" /opt/fizzmine/agent && rm -rf "$TMP"
[ -d /opt/fizzmine/venv ] || python3 -m venv /opt/fizzmine/venv
/opt/fizzmine/venv/bin/pip install -q --upgrade -r /opt/fizzmine/agent/requirements.txt

say "Enrolling $NAME with the hub"
INFO=$(python3 -c 'import json, platform; print(json.dumps({"kernel": platform.release(), "cpu": open("/proc/cpuinfo").read().split("model name")[1].split(":")[1].split("\n")[0].strip()}))' 2>/dev/null || echo '{}')
RESP=$(curl -fsS -X POST "$HUB/api/enroll" -H 'Content-Type: application/json' \
  -d "{\"key\": \"$KEY\", \"rig\": \"$NAME\", \"info\": $INFO}") || die "hub refused enrollment (wrong --key?)"
TOKEN=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])' <<<"$RESP")
PORT=$(grep -oP '^wildrig_api_port\s*=\s*\K[0-9]+' /etc/fizzmine/agent.toml 2>/dev/null || echo 60050)
cat > /etc/fizzmine/agent.toml <<EOF
# written by install-rig.sh; rerun the installer to change the hub or rig name
rig = "$NAME"
hub_url = "${HUB/http/ws}"
token = "$TOKEN"
interval_s = 5
wildrig_api_port = $PORT
EOF
chmod 600 /etc/fizzmine/agent.toml

cp /opt/fizzmine/agent/fizzagent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable fizzagent >/dev/null 2>&1
systemctl restart fizzagent

say "Done: $NAME is enrolled. Dashboard: $HUB"
if [ "$NEED_REBOOT" -eq 1 ]; then
  if [ "$REBOOT" -eq 1 ]; then
    echo "Rebooting in 10 seconds to load the new NVIDIA driver (Ctrl+C to cancel)..."
    sleep 10 && reboot
  else
    echo "Reboot to load the new NVIDIA driver."
  fi
fi

#!/bin/bash
# fizzmineOS rig installer. The hub serves this file with its own address filled in, so on a
# fresh Ubuntu Server 26.04 rig you only run:
#
#   curl -fsSL http://<hub>:8780/install.sh | sudo bash -s -- --key <enroll key>
#
# Options:
#   --key KEY              enroll key from the hub (required)
#   --name NAME            rig name on the dashboard (default: hostname)
#   --driver VER           NVIDIA driver: "recommended", an Ubuntu branch like 580, an exact NVIDIA
#                          release like 610.43.02 (from NVIDIA's site, like HiveOS does), "keep" or "skip"
#                          (default: recommended when no driver is installed, keep otherwise)
#   --tailscale-key KEY    join your tailnet with this auth key
#   --no-reboot            don't reboot after a driver install
# Running it again is safe: it updates the agent and re-enrolls the rig.
set -euo pipefail

HUB="__HUB_URL__"
KEY="" NAME="$(hostname)" DRIVER="" TS_KEY="" REBOOT=1 NEED_REBOOT=0 REBOOT_TO_FINISH=0

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

# An exact NVIDIA release from NVIDIA's download site (.run installer), built with DKMS so kernel
# updates rebuild it. Ubuntu's driver packages come off first so the two don't fight.
install_nvidia_run() {
  local ver="$1" run="/opt/fizzmine/drivers/NVIDIA-Linux-x86_64-$1.run"
  local url="https://us.download.nvidia.com/XFree86/Linux-x86_64/$1/NVIDIA-Linux-x86_64-$1.run"
  mkdir -p /opt/fizzmine/drivers
  if [ ! -s "$run" ]; then
    echo "  downloading NVIDIA $ver"
    curl -fL --retry 3 -o "$run.part" "$url" || die "couldn't download $url (is $ver a real release? see nvidia.com/drivers)"
    mv "$run.part" "$run"
  fi
  apt-get install -y -q build-essential dkms pkg-config "linux-headers-$(uname -r)" >/dev/null \
    || die "couldn't install the kernel headers the driver needs"
  if mokutil --sb-state 2>/dev/null | grep -qi enabled; then
    die "Secure Boot is on, and it won't load NVIDIA's own driver. Turn it off in the BIOS, or use an Ubuntu branch like --driver 610"
  fi
  # Ubuntu's driver packages, but not the container toolkit (Docker GPU access for AI apps)
  local pkgs
  pkgs=$(dpkg-query -W -f '${Package} ${Status}\n' 2>/dev/null | awk '/ installed$/ {print $1}' \
    | grep -E '^(nvidia-|libnvidia-)' | grep -v container || true)
  if [ -n "$pkgs" ]; then
    echo "  removing Ubuntu's NVIDIA packages"
    # shellcheck disable=SC2086
    apt-get purge -y -q $pkgs >/dev/null
  fi
  printf 'blacklist nouveau\noptions nouveau modeset=0\n' > /etc/modprobe.d/fizzmine-nouveau.conf
  update-initramfs -u >/dev/null 2>&1 || true  # so nouveau stays off at boot too
  # the driver can only be replaced while nothing uses the cards
  systemctl stop fizzagent nvidia-persistenced 2>/dev/null || true
  tmux kill-server 2>/dev/null || true
  for m in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do rmmod "$m" 2>/dev/null || true; done
  if lsmod | grep -q '^nvidia '; then
    NEED_REBOOT=1
    echo
    echo "  The old driver is still loaded (something is using the cards). Reboot, then run this same"
    echo "  command again to finish installing $ver. Until then the rig has no NVIDIA driver."
    echo
    REBOOT_TO_FINISH=1
    return
  fi
  echo "  installing NVIDIA $ver (a few minutes on an old CPU)"
  # RTX 20-series and newer use the open kernel modules (50-series requires them)
  sh "$run" --silent --dkms --no-questions --ui=none --kernel-module-type=open --no-x-check \
    || die "NVIDIA installer failed; its log is /var/log/nvidia-installer.log"
  NEED_REBOOT=1
}

if lspci -nn | grep -i '\[10de:' >/dev/null; then
  CURRENT=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)
  [ -n "$DRIVER" ] || { [ -n "$CURRENT" ] && DRIVER=keep || DRIVER=recommended; }
  say "NVIDIA driver (installed: ${CURRENT:-none}, requested: $DRIVER)"
  RECOMMENDED=$(ubuntu-drivers devices 2>/dev/null | awk '/recommended/{print $3}' | head -1 || true)
  case "$DRIVER" in
    keep|skip) PKG="" ;;
    recommended) PKG="$RECOMMENDED"; [ -n "$PKG" ] || echo "  ubuntu-drivers found no recommended driver; try --driver 580" ;;
    [0-9]*.[0-9]*)
      # an exact release: NVIDIA's own installer, not Ubuntu's package
      if [ "$CURRENT" = "$DRIVER" ]; then echo "  already on $DRIVER"; else install_nvidia_run "$DRIVER"; fi
      PKG="" ;;
    [0-9]*)
      # RTX 20-series and newer use the open kernel modules (required for 50-series)
      if [[ "$RECOMMENDED" == *-open ]]; then PKG="nvidia-driver-$DRIVER-open"; else PKG="nvidia-driver-$DRIVER"; fi ;;
    *) die "--driver must be recommended, keep, skip, a branch like 580 or a release like 610.43.02" ;;
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
# fizz: control the rig over SSH, even when the hub is down (fizz --help)
cat > /usr/local/bin/fizz <<'FIZZ'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
cd /opt/fizzmine/agent && exec /opt/fizzmine/venv/bin/python -m fizzagent.cli "$@"
FIZZ
chmod 755 /usr/local/bin/fizz
systemctl daemon-reload
systemctl enable fizzagent >/dev/null 2>&1
systemctl restart fizzagent

say "Done: $NAME is enrolled. Dashboard: $HUB"
if [ "$REBOOT_TO_FINISH" -eq 1 ]; then
  echo "After the reboot, run this same command again to finish the NVIDIA $DRIVER install."
fi
if [ "$NEED_REBOOT" -eq 1 ]; then
  if [ "$REBOOT" -eq 1 ]; then
    echo "Rebooting in 10 seconds to load the new NVIDIA driver (Ctrl+C to cancel)..."
    sleep 10 && reboot
  else
    echo "Reboot to load the new NVIDIA driver."
  fi
fi

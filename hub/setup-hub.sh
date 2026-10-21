#!/bin/bash
# fizzmineOS hub setup, for the always-on mini PC. On a fresh Ubuntu Server install run:
#
#   curl -fsSL https://raw.githubusercontent.com/fizzix-is-fun/fizzmineOS/main/hub/setup-hub.sh | sudo bash
#
# Options:
#   --tailscale-key KEY   join your tailnet with this auth key
#   --branch NAME         git branch to run (default: main)
# Running it again updates the hub to the latest code and keeps its config and history.
set -euo pipefail

REPO="https://github.com/fizzix-is-fun/fizzmineOS.git"
SRC=/opt/fizzmine/src
BRANCH=main TS_KEY=""
ARGS=("$@")

while [ $# -gt 0 ]; do
  case "$1" in
    --tailscale-key) TS_KEY="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"

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
apt-get install -y -q git curl ca-certificates openssl >/dev/null

if ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker"
  # Ubuntu's own packages: no extra repo or download site to reach
  apt-get install -y -q docker.io docker-compose-v2 docker-buildx >/dev/null
  systemctl enable --now docker
fi

if [ -n "$TS_KEY" ]; then
  say "Joining Tailscale"
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  tailscale up --authkey="$TS_KEY" --hostname=fizzhub --ssh
fi

say "Getting fizzmineOS ($BRANCH)"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q origin "$BRANCH"
  git -C "$SRC" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO" "$SRC"
fi

# The copy of this script that's running may be older than the one just pulled; run the new one.
if [ -z "${FIZZ_REEXEC:-}" ] && ! cmp -s "$0" "$SRC/hub/setup-hub.sh" 2>/dev/null; then
  say "Setup script was updated, running the new version"
  FIZZ_REEXEC=1 exec bash "$SRC/hub/setup-hub.sh" "${ARGS[@]}"
fi

CONF="$SRC/hub/config/hub.toml"
if [ ! -f "$CONF" ]; then
  mkdir -p "$SRC/hub/config"
  sed "s/^enroll_key = .*/enroll_key = \"$(openssl rand -hex 16)\"/" "$SRC/hub/hub.example.toml" > "$CONF"
  chmod 600 "$CONF"
fi
# dashboard password (older configs from before the login existed get one too)
if ! grep -q '^admin_password' "$CONF"; then
  sed -i "0,/^enroll_key/s//admin_password = \"$(openssl rand -base64 12 | tr -d '/+=')\"\n&/" "$CONF"
fi

say "Starting the hub"
docker compose -f "$SRC/hub/compose.yaml" up -d --build --force-recreate

cat > /usr/local/bin/fizzhub-key <<EOF
#!/bin/bash
# prints the command that adds a rig to this hub
KEY=\$(grep -oP '^enroll_key\s*=\s*"\K[^"]+' "$CONF")
IP=\$(tailscale ip -4 2>/dev/null | head -1 || true)
[ -n "\$IP" ] || IP=\$(hostname -I | awk '{print \$1}')
echo "Dashboard password: \$(grep -oP '^admin_password\s*=\s*"\K[^"]+' "$CONF")"
echo "On each rig, run:"
echo "  curl -fsSL http://\$IP:8780/install.sh | sudo bash -s -- --key \$KEY"
EOF
chmod 700 /usr/local/bin/fizzhub-key

for i in $(seq 1 30); do curl -fs http://127.0.0.1:8780/api/rigs >/dev/null && break; sleep 2; done
curl -fs http://127.0.0.1:8780/api/rigs >/dev/null || die "hub didn't come up; check: docker compose -f $SRC/hub/compose.yaml logs"

IP=$(hostname -I | awk '{print $1}')
say "Hub is running: http://$IP:8780"
/usr/local/bin/fizzhub-key
echo "(run 'sudo fizzhub-key' any time to see this again; change the password in $CONF, then rerun this setup)"

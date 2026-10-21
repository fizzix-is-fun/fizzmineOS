# fizzmineOS install guide

You set up the hub once on the mini PC. After that, each rig is a fresh Ubuntu install plus one pasted command.

## What you need

- A USB stick (8 GB or larger) and [Ubuntu Server 24.04 LTS](https://ubuntu.com/download/server)
- [balenaEtcher](https://etcher.balena.io/) or Rufus to write the USB stick
- A Tailscale account, optional but recommended for reaching rigs from outside the house

## 1. Get a Tailscale auth key (optional, 2 minutes)

1. Go to <https://login.tailscale.com/admin/settings/keys>.
2. Click **Generate auth key**. Turn on **Reusable** so one key works for every machine, and set the expiry to 90 days.
3. Copy the key (`tskey-auth-...`). You'll paste it into the commands below.

## 2. Set up the hub (mini PC)

1. Write Ubuntu Server 24.04 to the USB stick and boot the mini PC from it.
2. Go through the installer with the defaults. On these two screens:
   - **Profile**: pick a username and password you'll remember.
   - **SSH**: tick **Install OpenSSH server**.
3. When it finishes, remove the USB stick and reboot. Log in, then run `hostname -I`. The first address shown, for example `192.168.1.50`, is the hub's IP.
4. Optional but good: in your router, reserve that IP for the mini PC so it never changes.
5. From your main PC, open a terminal and connect with `ssh <username>@<hub-ip>`.
6. Paste this command (leave off `--tailscale-key ...` if you skipped step 1):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/fizzix-is-fun/fizzmineOS/main/hub/setup-hub.sh | sudo bash -s -- --tailscale-key tskey-auth-XXXX
   ```

   > Until PR #1 is merged, use the branch instead:
   > `curl -fsSL https://raw.githubusercontent.com/fizzix-is-fun/fizzmineOS/phase1-watch/hub/setup-hub.sh | sudo bash -s -- --branch phase1-watch --tailscale-key tskey-auth-XXXX`

7. It takes a few minutes, installing Docker and building the hub. At the end it prints two things:
   - the dashboard address, `http://<hub-ip>:8780`, which you open in a browser
   - the **rig command**. Copy it somewhere; you'll use it on every rig. Run `sudo fizzhub-key` on the hub any time to see it again.

## 3. Add a rig

### A fresh rig (including the old 8-slot boards)

1. Install Ubuntu Server 24.04 the same way as the hub, with **Install OpenSSH server** ticked.
   - Old boards: if the USB stick won't boot, look in the BIOS for "Legacy" or "CSM" boot. Ubuntu supports both.
   - With many GPUs on risers, installing with only one card plugged in can be easier; add the rest afterwards.
2. Connect from your main PC with `ssh <username>@<rig-ip>`.
3. Paste the rig command from the hub, adding your Tailscale key and a name for the dashboard:

   ```bash
   curl -fsSL http://<hub-ip>:8780/install.sh | sudo bash -s -- --key <enroll-key> --name oldboard1 --tailscale-key tskey-auth-XXXX
   ```

4. The command installs the recommended NVIDIA driver, the agent, and Tailscale, enrolls the rig, then reboots. A minute after the reboot the rig appears on the dashboard.

### A rig that already runs Ubuntu (armadilloai, babyarmadilloai)

Run the same rig command. Your current NVIDIA driver is kept unless you ask for a different one, and the `mine`, `ai` and `clocks` commands keep working. Then, so the dashboard can see hashrate, add `--api-port 60050` to the wildrig line in `/usr/local/bin/mine`:

```bash
grep -q -- --api-port /usr/local/bin/mine || sudo sed -i 's|--pearlhash-kernel 2|--pearlhash-kernel 2 --api-port 60050|' /usr/local/bin/mine
```

## Choosing an NVIDIA driver

Add `--driver` to the rig command:

| Option | What it does |
| --- | --- |
| `--driver recommended` | Ubuntu's recommended driver for the cards (the default on a fresh rig) |
| `--driver 580` | that version; open kernel modules on RTX 20-series and newer |
| `--driver keep` | leave the installed driver alone (the default when one is installed) |
| `--driver skip` | don't touch drivers at all |

To see which versions are available on a rig, run `ubuntu-drivers list`. Add `--no-reboot` to reboot later yourself.

## Updating

- **Hub:** rerun the hub command. It pulls the latest code and keeps your settings and history.
- **Rig:** rerun the rig command. It updates the agent and re-enrolls the rig under the same name.

## When something looks wrong

| Problem | Check |
| --- | --- |
| Rig doesn't show up | On the rig: `journalctl -u fizzagent -n 50` |
| Rig shows "AI / idle" while mining | `--api-port 60050` is missing from the miner command |
| No GPUs listed after a driver install | Reboot, then run `nvidia-smi` on the rig |
| Dashboard doesn't load | On the hub: `docker compose -f /opt/fizzmine/src/hub/compose.yaml logs` |

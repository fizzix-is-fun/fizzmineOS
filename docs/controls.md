# Running your rigs from the dashboard

Once the agent on a rig is updated, the dashboard can start and stop mining, overclock each card, and switch the rig between mining and AI. Each rig keeps its settings in `/etc/fizzmine/state.json`, so after a reboot it comes back the way you left it: mining with the same flight sheet and OC, in AI mode, or stopped.

## Logging in

The dashboard asks for a password. On the hub, `sudo fizzhub-key` shows it. To change it, edit `admin_password` in `/opt/fizzmine/src/hub/config/hub.toml`, then rerun the hub setup.

## Flight sheets

A flight sheet says what to mine and with which miner:

| Field | Example |
| --- | --- |
| Miner | WildRig-NG, or Custom for any other miner |
| Miner download URL | the miner's release `.tar.gz`, `.tar.xz` or `.zip`; each rig downloads it once into `/opt/fizzmine/miners/` |
| Or: miner already on the rig | `/home/scort/wildrig/ng-1.0.0l` (a folder or the program itself) |
| Algorithm / Pool / Wallet | `pearlhash` / `pool.pearlhash.xyz:9000` / `prl1…@nock=…` |
| Worker name | `%RIG%` becomes the rig's name |
| Extra arguments | `--pearlhash-kernel 2` |

For a custom miner, also give the program's name in the download and its full arguments, using `{algo} {pool} {wallet} {worker} {api_port}` where those go.

To use it, tick the rigs in the list, leave "start mining" ticked, and click **Push**. A rig that's already mining restarts with the new sheet.

## Watching the miner

Click **Miner console** on a rig to open its miner output in a new tab, updating every couple of seconds, like the miner window in HiveOS. Scroll up to pause it; scroll back to the bottom to follow again.

The miner runs in a tmux session on the rig, so you can also watch it there:

```bash
sudo tmux attach -t miner      # Ctrl+B then D to leave it running
```

The log is also written to `/var/log/fizzmine/miner.log`. When it passes 20 MB, the next miner start moves it to `miner.log.1`.

## Overclocking

Click **Overclock** on a rig. For each card you can set the power limit (W), a core clock lock, a core offset, a memory lock, a memory offset, and the fans: auto, a fixed %, or a target temperature that the agent holds by adjusting the fans every few seconds (fans go to 100% at 85 °C). A blank field means stock. **Copy to all** copies one card's row to every card on that rig.

OC is applied while the rig is mining. Switching to AI mode or stopping the miner puts the cards back to stock clocks and automatic fans.

**OC profiles** do the same for every card of one model on every rig, for example all RTX 5060 Tis at once. Save a profile, then click **Apply to all**.

## AI mode

**AI mode settings** holds two commands the rig runs: one when switching to AI, and one when switching back to mining. For babyarmadilloai:

- When switching to AI mode: `docker compose -f /home/scort/homechat/compose.yaml start ollama`
- When switching to mining: `docker compose -f /home/scort/homechat/compose.yaml stop ollama`

**Switch to AI** stops the miner, resets the cards to stock, then runs the first command. **Start mining** runs the second, applies your OC, and starts the miner.

## Moving a rig off the old `mine` and `ai` commands

The dashboard replaces `mine`, `ai`, `clocks`, `gpuoffset` and `gpufans.py`. So that two things don't fight over the cards:

1. Stop the old miner if it's running: `tmux kill-session -t mine`
2. On armadilloai, turn off the old fan loop: `sudo systemctl disable --now gpufans`
3. If a rig has `gpu-oc.service` (a memory offset at boot), turn it off and put the offset into the dashboard's OC instead: `sudo systemctl disable gpu-oc`
4. Make a flight sheet with the same pool, wallet, worker and `--pearlhash-kernel 2`, set your clocks under **Overclock**, and push it.

The old commands still work if you need them; just don't run them while the dashboard has the rig mining.

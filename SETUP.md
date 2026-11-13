# Exposing each Ollama node on the LAN

By default Ollama binds to `127.0.0.1` and refuses connections from other
machines. Each box needs one environment variable to change that, plus a
firewall hole. Everything below is per-machine; do it once on each node.

The variables worth setting on every node:

| Variable | Value | Why |
| --- | --- | --- |
| `OLLAMA_HOST` | `0.0.0.0:11434` | Listen on the LAN, not just loopback. Required. |
| `OLLAMA_KEEP_ALIVE` | `-1` on the dedicated boxes, `2m` on the Mac | Keeps the model resident so requests don't pay a cold load. Pin it on machines that do nothing else; on the laptop you work on, a short timeout is what you want instead. |
| `OLLAMA_FLASH_ATTENTION` | `1` | Cheaper attention, less KV-cache memory. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Roughly halves KV-cache memory. On the 4070 that memory goes back to the weights. |
| `OLLAMA_NUM_PARALLEL` | `1` on the 26b box, `2` elsewhere | Each parallel slot allocates its own KV cache. |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Stops a stray request for another tag evicting the resident model. |

These values are identical on every machine. `0.0.0.0` is a bind directive
meaning "listen on all interfaces of this host", not a destination, so nothing
here needs to be customised per box. The only per-machine value in the whole
system is each node's LAN address, and that lives in `config.json` on the Mac.

Be aware that `OLLAMA_HOST` means two different things depending on which side
of the connection reads it. To the **server** it is the bind address. To the
**`ollama` CLI** it is the server to talk to. That makes it easy to manage the
whole pool from the Mac without touching the other machines:

```bash
OLLAMA_HOST=10.0.0.22:11434 ollama pull gemma4:e4b   # pull onto that box
OLLAMA_HOST=10.0.0.22:11434 ollama ps                # what it has resident
OLLAMA_HOST=10.0.0.22:11434 ollama list              # what it has on disk
```

It also means that if you ever `export OLLAMA_HOST=<remote>` in your Mac shell,
plain `ollama list` will report the remote box's models and look like your local
install lost everything. Prefer the one-off prefix form above.

---

## Linux (systemd — the normal install)

Ollama installs as a systemd service, so environment variables have to go in a
unit override rather than your shell profile.

```bash
sudo systemctl edit ollama.service
```

That opens an editor. Add this block above the commented-out region:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

Save, then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
systemctl show ollama --property=Environment    # confirm they took
```

Open the port. On Ubuntu/Debian with ufw:

```bash
sudo ufw allow 11434/tcp
```

On Fedora/RHEL with firewalld:

```bash
sudo firewall-cmd --permanent --add-port=11434/tcp && sudo firewall-cmd --reload
```

Find the machine's address, which is what you register with the router. Don't
use `hostname -I` — it lists every interface including Docker bridges
(`172.17.x.x`), libvirt (`192.168.122.1`) and Tailscale (`100.x.x.x`), and the
first one is not reliably the right one. Ask the kernel which address it would
use to reach the Mac instead:

```bash
ip route get 10.0.0.13
```

The value printed after `src` is the address to register. If nothing comes back
on `10.0.0.x`, that machine is not on the same segment as the Mac and no amount
of Ollama configuration will fix it.

### Linux without systemd

Run it in the foreground with the variables inline, or put this in a tmux session:

```bash
OLLAMA_HOST=0.0.0.0:11434 OLLAMA_KEEP_ALIVE=-1 \
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
ollama serve
```

---

## Windows

Ollama on Windows runs as a normal user application from the system tray, not
as a service. It reads its environment once at launch, so the order matters:
set the variables first, then fully restart the app.

**1. Quit Ollama.** Right-click the llama icon in the system tray and choose
Quit. Closing the window is not enough.

**2. Set the variables.** In PowerShell (no admin needed):

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "-1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")
```

The GUI equivalent is Settings → System → About → Advanced system settings →
Environment Variables → New, under *User variables*.

**3. Open the firewall.** In PowerShell **as Administrator**:

```powershell
New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound `
  -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private
```

`-Profile Private` matters. If your network is classified as Public, Windows
will drop the traffic anyway — check with `Get-NetConnectionProfile` and change
the category to Private if needed.

**4. Relaunch Ollama** from the Start menu.

**5. Find the address:**

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" } | Select-Object IPAddress, InterfaceAlias
```

---

## macOS (this machine, as an overflow node)

**You probably need nothing here.** The Mac is registered as `127.0.0.1` in
`config.json`, so it already works as an overflow node. `OLLAMA_HOST` only
matters if you want the *other* machines to call the Mac.

Do **not** set `OLLAMA_KEEP_ALIVE=-1` on this machine. That variable pins a
model in memory permanently, which is right for a dedicated box and wrong for a
16GB laptop you also work on. Leave the default 5-minute timeout, or shorten it:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE "2m"
```

If you do want the Mac reachable from the other machines, note that
`launchctl setenv` is session state and is gone after a reboot. Making it stick
means running the server yourself from a LaunchAgent instead of the tray app.
Write `~/Library/LaunchAgents/com.local.ollama.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>          <string>com.local.ollama</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/ollama</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key>            <string>0.0.0.0:11434</string>
    <key>OLLAMA_KEEP_ALIVE</key>      <string>2m</string>
    <key>OLLAMA_FLASH_ATTENTION</key> <string>1</string>
    <key>OLLAMA_MAX_LOADED_MODELS</key> <string>1</string>
  </dict>
  <key>RunAtLoad</key>  <true/>
  <key>KeepAlive</key>  <true/>
  <key>StandardErrorPath</key> <string>/tmp/ollama.err</string>
</dict>
</plist>
```

Then quit Ollama.app, remove it from System Settings → General → Login Items so
the two don't fight over port 11434, and load the agent:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.ollama.plist
```

The `ollama` CLI still works normally against this server — `ollama pull`,
`ollama list` and `ollama ps` all talk to whatever is listening on 11434.

To undo it: `launchctl bootout gui/$(id -u)/com.local.ollama` and re-enable the app.

---

## Do these survive a reboot?

| Machine | Method | Persistent? |
| --- | --- | --- |
| Linux | `systemctl edit ollama.service` | **Yes.** Writes `/etc/systemd/system/ollama.service.d/override.conf`, read at every service start. Survives Ollama upgrades too, since it's a separate drop-in file. |
| Windows | `SetEnvironmentVariable(..., "User")` | **Yes.** Writes to `HKCU\Environment` in the registry. Ollama auto-starts at login and reads it. |
| macOS | `launchctl setenv` | **No.** Session state only, gone at reboot. Use the LaunchAgent above if you need it. |

The Windows one has a trap worth knowing: `$env:OLLAMA_HOST = "0.0.0.0:11434"`
sets the variable for *that PowerShell window only* and vanishes when you close
it. Only the `SetEnvironmentVariable` form with `"User"` (or the GUI dialog, or
`setx`) writes to the registry. If Ollama works right after you set it and
stops working tomorrow, that's the mistake.

Confirm the value actually persisted, after a reboot, on the machine itself:

```bash
systemctl show ollama --property=Environment          # linux
```

```powershell
[Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User")   # windows
```

---

## Verify and register

From the Mac, check each node answers over the network:

```bash
curl -s http://<node-ip>:11434/api/tags | head -c 300
```

If that returns JSON, register it:

```bash
python3 swarm.py add e2b  <node-ip> --os windows --bias 1.0
python3 swarm.py add e4b  <node-ip> --os linux   --bias 1.2
python3 swarm.py add g26b <node-ip> --os windows --bias 1.0 --max-parallel 1
```

Models are autodetected from `/api/tags`. Pass `--model gemma4:e4b` explicitly
if you want a node restricted to one tag.

Then confirm the whole pool and take baseline measurements:

```bash
python3 swarm.py nodes
python3 swarm.py bench
```

`bench` is not optional. Until it runs, the router is using the guessed
`decode_tps_hint` values from `config.json`, and the guess for the 26b box in
particular is unlikely to match what a spilling 4070 actually does.

---

## Give the nodes stable addresses

DHCP will eventually reassign an address and the router will start reporting a
node as down. Either reserve each MAC address in your router's DHCP settings,
or use mDNS names — every modern OS resolves `<hostname>.local`, so
`python3 swarm.py add e4b workshop.local` survives address changes.

---

## Troubleshooting

**`syntax error near unexpected token '"OLLAMA_HOST",'`.** You ran the
PowerShell command in bash or zsh. `[Environment]::SetEnvironmentVariable` is
PowerShell-only and has to run on the Windows machine itself — not in Terminal
on the Mac, not over SSH, not in WSL or Git Bash. Check which shell you are in
with `$PSVersionTable`, which prints a table in PowerShell and errors anywhere
else. If you would rather avoid PowerShell, `setx OLLAMA_HOST "0.0.0.0:11434"`
does the same thing and works in `cmd.exe` too.

**`curl` works locally on the node but not from the Mac.** `OLLAMA_HOST` didn't
take. On Windows this is almost always because the app wasn't fully quit before
the variables were set. Check with `curl -s localhost:11434` on the node itself
and look at what address `ollama serve` reports it is listening on.

**Connection times out rather than being refused.** That's a firewall, not
Ollama. Refused means nothing is listening; timeout means something is dropping
packets in between.

**The 26b box is far slower than expected.** Run `ollama ps` on it. The output
shows the CPU/GPU split, and any non-zero CPU percentage means part of the
model is in system RAM. Options, in order of impact: set `OLLAMA_KV_CACHE_TYPE=q8_0`,
lower `OLLAMA_NUM_PARALLEL` to 1, pull a smaller quantization, or move that
box's traffic to `gemma4:12b` which fits a 12GB card whole. Compare with
`python3 swarm.py bench --node <name>` before and after each change.

**A model unloads between requests.** `OLLAMA_KEEP_ALIVE=-1` wasn't applied.
Confirm with `ollama ps`, which shows an expiry time per loaded model.

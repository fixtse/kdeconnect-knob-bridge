# KDE Connect → Home Assistant → ESPHome knob

Forward new phone notifications already received by your Linux desktop's KDE
Connect to an [OpenNextion knob](https://github.com/fixtse/OpenNextion-KNOB-ESPHome).
Uses the existing pairing; no KDE Home Assistant integration or HA API token.
Works with Niri/Wayland/Noctalia and other Linux desktop sessions.

- Watches KDE Connect D-Bus notification-added/updated/ready signals.
- Publishes non-retained JSON over MQTT, with app filtering and duplicate suppression.
- Copies available notification PNGs into a bounded in-memory cache. A small HTTP
  endpoint supplies an `image_url` the ESP32 can download.
- Sends title, message, image_url and timeout_seconds to the knob's `show_message`.
- Does not replay existing notifications on startup or dismiss notifications on the phone.

## Requirements

Python 3.11+, PyGObject/Gio, KDE Connect running in your desktop user session,
mosquitto_pub (or access to it in a Docker container), and Home Assistant's MQTT
and ESPHome integrations. No pip dependencies.

CachyOS/Arch packages: `python`, `python-gobject`, `kdeconnect`, `mosquitto`.
Debian/Ubuntu: `python3`, `python3-gi`, `gir1.2-glib-2.0`, `kdeconnect`, `mosquitto-clients`.
Install packages using your distribution package manager if missing.

Pair the phone with KDE Connect, grant notification access on Android, and enable
notification sharing. The knob needs firmware exposing `show_message(title,
message, image_url, timeout_seconds)`; older text-only firmware is incompatible
with the supplied automation.

## Install

```sh
git clone https://github.com/fixtse/kdeconnect-knob-bridge ~/.local/share/kdeconnect-knob-bridge
cd ~/.local/share/kdeconnect-knob-bridge
mkdir -p ~/.config/kdeconnect-knob-bridge ~/.config/systemd/user
cp examples/config.toml ~/.config/kdeconnect-knob-bridge/config.toml
kdeconnect-cli --list-available --id-name-only
```

Edit your private config: phone device IDs, MQTT command/topic, desktop LAN image
address, knob client IP and KDE cache directory. Set `images.enabled = false` for
text only. `include_apps` and `exclude_apps` match app names exactly. Silent
notifications are skipped by default; set `ignore_silent = false` to include them.

Add `examples/home-assistant-automation.yaml` in the HA automation editor, replacing
the ESPHome action name and MQTT topic. For file-managed HA installations, back up
and validate the configuration before reloading automations. Enable the service:

```sh
cp examples/kdeconnect-knob-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kdeconnect-knob-bridge.service
journalctl --user -u kdeconnect-knob-bridge.service -n 30
```

Allow the knob to reach TCP 18767 on the desktop. The default service expects the
checkout under `~/.local/share/kdeconnect-knob-bridge`; adjust WorkingDirectory
if installed elsewhere. Run as the desktop user, not root or inside HA's container.
Foreground: `python3 -m knob_bridge --config /path/to/config.toml`.

## Images and behavior

KDE Connect exposes `iconPath`: this may contain an app icon, avatar or other image,
not necessarily an attached photo. Only PNGs inside configured cache roots are
accepted (512 KiB and 2048×2048 maximum). The knob does the final resize; its current
notification image area is 112×64. Unsupported/missing images fall back to text.
Image URLs use random tokens, expire after 15 minutes and disappear on restart;
at most 32 images are held. Notification queues and duplicate tracking are bounded.

The image HTTP endpoint has no login and uses plain LAN HTTP unless you place it
behind an HTTPS proxy. Restrict `allowed_clients` to the knob and keep the port off
the public Internet. MQTT carries notification content: use a trusted broker and
broker access controls. Keep passwords in external client configuration. Neither
notification text nor image URL tokens are logged. No notification data is written
to disk by the bridge.

The knob retains its own rules: press dismisses, turn scrolls, timers take priority,
and only the newest notification is kept. HA ignores messages older than 30 seconds.
The bridge sends only new signals; KDE may resend notifications after reconnecting,
and identical updates are suppressed for two minutes within each bridge process.
Failures are logged without content and do not trigger unbounded retries. A later
KDE notification update can retry failed MQTT publication.

## Development

Open in GitHub Codespaces or a devcontainer. The Dockerfile supplies Python, Gio,
a session bus and MQTT clients. No application ports are forwarded automatically.

```sh
/usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 -m compileall -q knob_bridge tests
```

Tests cover UTF-8 clipping, filters, deduplication, bounded/expiring image paths,
D-Bus signal routing, config validation and non-retained MQTT publication. Real
phone delivery needs a desktop KDE Connect session; Codespaces cannot receive
your phone's desktop session signals.

## License

MIT. This is an independent bridge, not an official KDE or Home Assistant project.

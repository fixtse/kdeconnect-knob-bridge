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
- Clears the matching knob popup when KDE Connect removes a phone notification.
- Includes a local app-selection UI with saved choices and a pause switch.
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

Create a Text helper named `input_text.knob_notification_active` (maximum length 255),
or use the YAML helper example in the automation file. Then add
`examples/home-assistant-automation.yaml` in the HA automation editor, replacing
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

## Choose apps and sync dismissal

Open **http://127.0.0.1:18769** on the desktop running the bridge. For an app-menu
shortcut, copy `examples/kdeconnect-knob-settings.desktop` to
`~/.local/share/applications/`. The settings page
lets you pause sharing, choose each app, set whether new apps share by default,
include silent notifications and choose when popups clear. The default is
**30 seconds or when the phone clears it**: either event dismisses the popup,
whichever happens first. Enter any whole number of seconds from **1 to 3600**
in the **Timeout in seconds** field. Enter **0** to disable the timeout and wait
for phone or manual dismissal. Pressing the knob always dismisses the popup. Settings apply
immediately without restarting the bridge. Blocking the currently displayed app
also clears that bridge popup.

Apps are discovered from active KDE Connect notifications on startup and as new
notifications arrive. Startup discovery does not forward old notifications. You can
also add an exact app name manually. Only app names and preferences are saved in
`preferences.json` alongside `config.toml`; notification messages and pictures are
not stored. Once preferences exist, edit sharing choices in the UI rather than
TOML. Settings listen only on loopback and are not exposed on the image server;
for a remote desktop, use SSH local forwarding of TCP 18769. The page checks Host,
Origin and a per-process request token for settings requests.

KDE Connect sends removal signals when Android removes a notification. The bridge
forwards those removals, and HA compares the notification ID before dismissing the
popup: clearing an old notification cannot clear a newer one. Device-wide clears
are matched to that phone. Merely viewing a message cannot be detected if its app
keeps the Android notification. This is the same notification lifecycle available
to the desktop. Pressing the knob does not dismiss anything on your phone.

The HA helper tracks the most recent bridge popup. The knob currently exposes no
message ID in its dismiss action: if an unrelated automation replaces a bridge
popup, HA cannot identify that replacement and a later phone removal may clear it.
For shared reminder displays, clear `input_text.knob_notification_active` before
showing a non-phone reminder, or route all reminders through a single owner script.

## Home Assistant automation examples

Replace the ESPHome device prefix and MQTT topic with your own. The topic in the
examples is `desktop/knob/notification`; use the same topic in `config.toml`.
These examples use the image-capable `show_message` action, even for text-only
popups, so `image_url` is always supplied (empty for text only).

### Forward phone notifications and sync dismissal

Create this helper in `configuration.yaml` (merge it into an existing
`input_text:` section if you have one), or create a Text helper with the same
entity ID and a maximum length of 255:

```yaml
input_text:
  knob_notification_active:
    name: Active knob phone notification
    max: 255
    initial: ""
```

Paste this automation into Home Assistant's automation YAML editor. It handles
new notifications, individual removals and device-wide clears. The bridge's
selected timeout is passed to the knob; the default is 30 seconds, while phone
removal can clear it earlier. A value of `0` deliberately disables the timeout.
An old removal is ignored if a newer notification is active.

```yaml
alias: KDE Connect notifications on knob
triggers:
  - trigger: mqtt
    topic: desktop/knob/notification
conditions:
  - condition: template
    value_template: >-
      {{ trigger.payload_json is mapping
         and trigger.payload_json.get('kind') in ['notification', 'dismiss', 'dismiss_device']
         and trigger.payload_json.get('device_id', '') | length > 0
         and 0 <= as_timestamp(now()) - (trigger.payload_json.get('timestamp', 0) | int(0)) < 30 }}
actions:
  - variables:
      event: "{{ trigger.payload_json }}"
      key: "{{ trigger.payload_json.device_id ~ '/' ~ trigger.payload_json.get('notification_id', '') }}"
  - choose:
      - conditions: "{{ event.kind == 'notification' and event.get('message', '') | length > 0 }}"
        sequence:
          - action: esphome.onx2424g013_dbbc54_show_message
            data:
              title: "{{ event.title }}"
              message: "{{ event.message }}"
              image_url: "{{ event.get('image_url', '') }}"
              timeout_seconds: "{{ event.get('timeout_seconds', 30) | int(30) }}"
          - action: input_text.set_value
            target:
              entity_id: input_text.knob_notification_active
            data:
              value: "{{ key }}"
      - conditions: >-
          {{ (event.kind == 'dismiss' and states('input_text.knob_notification_active') == key)
             or (event.kind == 'dismiss_device' and states('input_text.knob_notification_active').startswith(event.device_id ~ '/')) }}
        sequence:
          - action: esphome.onx2424g013_dbbc54_dismiss_message
          - action: input_text.set_value
            target:
              entity_id: input_text.knob_notification_active
            data:
              value: ""
mode: queued
max: 32
```

The same automation is available in
[`examples/home-assistant-automation.yaml`](examples/home-assistant-automation.yaml).
Back up file-managed HA configuration, run its configuration check and reload
the affected helper/automation domains after installation.

### Show a reminder with a timeout

This independent automation displays a reminder after a door has been open for
five minutes. Clear the bridge helper first so a later phone dismissal cannot
clear this unrelated reminder. Replace the example door entity.

```yaml
alias: Door left open on knob
triggers:
  - trigger: state
    entity_id: binary_sensor.front_door
    to: "on"
    for: "00:05:00"
actions:
  - action: input_text.set_value
    target:
      entity_id: input_text.knob_notification_active
    data:
      value: ""
  - action: esphome.onx2424g013_dbbc54_show_message
    data:
      title: Front door
      message: The front door has been open for five minutes.
      image_url: ""
      timeout_seconds: 30
mode: single
```

### Show an image or keep a reminder until dismissed

Use this action sequence in an automation or script. The URL must be reachable
by the knob; a path on the desktop is not a usable image URL. Set
`timeout_seconds: 0` to keep the reminder until the knob is pressed or an
explicit `dismiss_message` action clears it. Set a positive value for an
automatic timeout instead. This independent reminder has no phone notification
to synchronize with.

```yaml
sequence:
  - action: input_text.set_value
    target:
      entity_id: input_text.knob_notification_active
    data:
      value: ""
  - action: esphome.onx2424g013_dbbc54_show_message
    data:
      title: Reminder
      message: Collect the parcel at the front door.
      image_url: "http://homeassistant.local:8123/local/parcel.png"
      timeout_seconds: 0
```

To clear a reminder from another automation, use:

```yaml
action: esphome.onx2424g013_dbbc54_dismiss_message
```

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
The bridge forwards only new signals; KDE may resend notifications after reconnecting,
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
D-Bus signal routing, removal identity matching, persistent app preferences, settings
HTTP authorization, config validation and non-retained MQTT publication. Real
phone delivery needs a desktop KDE Connect session; Codespaces cannot receive
your phone's desktop session signals.

## License

MIT. This is an independent bridge, not an official KDE or Home Assistant project.

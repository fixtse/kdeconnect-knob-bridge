# KDE Connect knob bridge (archived)

**This MQTT bridge has been retired and replaced by direct KDE Connect notification receiving in Home Assistant.** This repository is archived and no longer maintained. Existing code and the [legacy instructions](LEGACY.md) remain available for reference.

## Use the new setup

The replacement consists of:

1. **[KDE for Home Assistant - notification-enabled fork](https://github.com/fixtse/kdeforhomeassistant#notifications-from-devices)** - receives notifications directly from paired phones and computers. Choose which apps are shared independently for each device, and forward notifications and images to an ESPHome display.
2. **[KDE Connect Rich Notifications](https://github.com/fixtse/kdeconnect-rich-notifications)** - an optional Linux desktop plugin that forwards chat avatars, website icons and screenshot previews through the existing KDE Connect connection. See its [installation guide](https://github.com/fixtse/kdeconnect-rich-notifications#add-the-plugin-to-kde-connect).

**Use the `fixtse/kdeforhomeassistant` fork for now.** Notification receiving is proposed upstream in [Radeaus/kdeforhomeassistant PR #3](https://github.com/Radeaus/kdeforhomeassistant/pull/3), which is not yet merged as of 2026-11-17. The links and blueprint below deliberately point to the fork.

### 1. Install notification receiving in Home Assistant

In HACS, add `https://github.com/fixtse/kdeforhomeassistant` as a custom repository with type **Integration**, install **KDE for Home Assistant**, and restart Home Assistant. Follow the [fork's setup guide](https://github.com/fixtse/kdeforhomeassistant#getting-started).

Pair each notification source **directly with Home Assistant** from the integration's sidebar. A phone paired only with a desktop is not automatically paired with Home Assistant.

- **Android:** enable notification sync for the Home Assistant pairing and grant notification access in KDE Connect.
- **Linux desktop:** enable notification sending for the Home Assistant pairing. For rich previews, follow the Rich Notifications plugin's build and installation guide, enable **Rich notifications**, and disable the stock **Send notifications** plugin for that same receiver to prevent duplicates. The plugin currently requires a compatible Qt 6 KDE Connect installation and matching source headers; check its guide for tested versions.
- In Home Assistant's sidebar, enable **Share notifications** for each intended source. Apps appear after their first notification. For example, allow Telegram from the desktop and block it on the phone to avoid displaying it twice.

Phone notifications go straight to Home Assistant; a desktop does not need to relay them. The Rich Notifications plugin is for Linux desktop sources and is not needed on the phone.

### 2. Connect the ESPHome display

Use the new [notifications-on-ESPHome-display blueprint](https://github.com/fixtse/kdeforhomeassistant/blob/main/blueprints/automation/notifications_on_esphome_display.yaml):

[![Import the replacement blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffixtse%2Fkdeforhomeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fnotifications_on_esphome_display.yaml)

Select the ESPHome display and a Text helper with a maximum length of 255. The display needs `show_message(title, message, image_url, timeout_seconds)` and `dismiss_message`, as provided by the compatible OpenNextion knob firmware.

If generated image URLs use an address the display cannot reach, set the blueprint's **Icon server URL** to Home Assistant's reachable LAN URL, including its port. Images are supplied by Home Assistant; the old desktop image server is no longer needed.

## Migrate from this bridge

1. Set up the integration and pair your notification sources directly with Home Assistant as described above.
2. Disable the old MQTT notification automation for the knob in Home Assistant. Keep it disabled while configuring the replacement automation so the same notification is not displayed twice.
3. Stop and disable this bridge's desktop service:

   ```sh
   systemctl --user disable --now kdeconnect-knob-bridge.service
   ```

   If you run the bridge another way, stop that process and remove its startup entry instead.
4. Import the replacement blueprint and create a new automation. It listens for `kdeforhomeassistant_notification` events instead of MQTT messages. You can reuse the existing Text helper after disabling the old automation.
5. Select apps per source device in Home Assistant and send a fresh notification. Check its text, avatar or preview, and dismissal behavior. Different clients supply different notification images and removal events.
6. Once the replacement works, remove the old bridge configuration and desktop image-server firewall rule if they are no longer used. Keep MQTT installed if other Home Assistant devices depend on it.

Archiving this repository does **not** stop services that you already installed. The replacement does not require this bridge, its local settings UI, MQTT forwarding, or its desktop HTTP image endpoint.

## Historical reference

- [Legacy installation, settings and development instructions](LEGACY.md)
- [Old MQTT blueprint](blueprints/automation/kdeconnect_knob_notifications.yaml), retained for existing installations
- [Replacement integration and per-device app controls](https://github.com/fixtse/kdeforhomeassistant#notifications-from-devices)
- [Rich Notifications plugin and installation guide](https://github.com/fixtse/kdeconnect-rich-notifications)

This archived bridge remains MIT-licensed. The replacement projects have their own licenses and documentation.

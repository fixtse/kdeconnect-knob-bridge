"""Run as the same desktop user as kdeconnectd; no root or HA token required."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import queue
import re
import signal
import subprocess
import threading
import tomllib
from pathlib import Path
from urllib.parse import urlparse
from .core import Deduplicator, ImageStore, notification
from .preferences import Preferences
from .settings import start_settings
import time

SERVICE = 'org.kde.kdeconnect'
NOTIFICATIONS = 'org.kde.kdeconnect.device.notifications'
ITEM = NOTIFICATIONS + '.notification'
PATTERN = re.compile(r'^/modules/kdeconnect/devices/([a-zA-Z0-9_-]+)/notifications(?:/([a-zA-Z0-9_-]+))?$')
LOG = logging.getLogger('knob-bridge')


def load_config(path):
    with open(path, 'rb') as source:
        config = tomllib.load(source)
    if not config.get('device_ids') or not all(re.fullmatch(r'[a-zA-Z0-9_-]+', d) for d in config['device_ids']):
        raise ValueError('device_ids must contain paired KDE Connect IDs')
    if not 0 <= config.get('timeout_seconds', 30) <= 3600:
        raise ValueError('timeout_seconds must be between 0 and 3600')
    mqtt = config.get('mqtt', {})
    if not isinstance(mqtt.get('command'), list) or not mqtt['command'] or not all(isinstance(x, str) for x in mqtt['command']):
        raise ValueError('mqtt.command must be a nonempty argv array')
    if not mqtt.get('topic') or any(c in mqtt['topic'] for c in '#+\x00'):
        raise ValueError('mqtt.topic must be an exact topic')
    if '-r' in mqtt['command'] or '--retain' in mqtt['command'] or '-l' in mqtt['command']:
        raise ValueError('Do not retain notification events or use line mode')
    images = config.get('images', {})
    if images.get('enabled', True):
        url = urlparse(images.get('base_url', ''))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('images.base_url must be an HTTP(S) origin reachable by the knob')
        if not images.get('allowed_roots'):
            raise ValueError('images.allowed_roots is required when images are enabled')
    return config


class Bridge:
    def __init__(self, config, Gio, GLib, preferences_path=None):
        self.config, self.Gio, self.GLib = config, Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.loop = GLib.MainLoop()
        self.jobs = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.dedupe = Deduplicator()
        images = config.get('images', {})
        self.images = ImageStore(images.get('base_url', ''), images.get('allowed_roots', []))
        self.http = None
        self.settings_http = None
        self.current = None
        self.preferences = Preferences(preferences_path or Path.home() / '.config/kdeconnect-knob-bridge/preferences.json', config)

    def properties(self, path):
        result = self.bus.call_sync(SERVICE, path, 'org.freedesktop.DBus.Properties',
                                   'GetAll', self.GLib.Variant('(s)', (ITEM,)), None,
                                   self.Gio.DBusCallFlags.NONE, 2000, None)
        return result.unpack()[0]

    def enqueue(self, device, identifier, kind='update'):
        if kind not in ('update', 'remove', 'clear', 'scan'):
            return
        if device not in self.config['device_ids'] or (kind in ('update', 'remove') and not re.fullmatch(r'[a-zA-Z0-9_-]+', identifier)):
            return
        try:
            self.jobs.put_nowait((kind, device, identifier))
        except queue.Full:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                pass
            try:
                self.jobs.put_nowait((kind, device, identifier))
            except queue.Full:
                pass
            LOG.warning('Notification queue full; discarded oldest event')

    def on_signal(self, connection, sender, path, interface, member, parameters, data):
        match = PATTERN.fullmatch(path)
        if not match:
            return
        device, identifier = match.groups()
        if member in ('notificationPosted', 'notificationUpdated'):
            values = parameters.unpack()
            if values and isinstance(values[0], str):
                self.enqueue(device, values[0])
        elif member == 'ready' and identifier:
            self.enqueue(device, identifier)
        elif member == 'notificationRemoved':
            values = parameters.unpack()
            if values and isinstance(values[0], str):
                self.enqueue(device, values[0], 'remove')
        elif member == 'allNotificationsRemoved':
            self.enqueue(device, '', 'clear')
        # Phone removals clear only the matching popup; never dismiss on the phone.

    def publish(self, payload):
        mqtt = self.config['mqtt']
        # mosquitto_pub reads one JSON message from stdin. Never retained.
        subprocess.run(mqtt['command'] + ['-q', '1', '-t', mqtt['topic'], '-s'],
                       input=json.dumps(payload, ensure_ascii=False).encode(),
                       check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def policy_changed(self):
        # Only the worker changes current-popup tracking. Queue this behind earlier events.
        try:
            self.jobs.put_nowait(('policy', '', ''))
        except queue.Full:
            LOG.warning('Policy saved; current popup will reconcile on the next event')

    def dismiss(self, device, identifier):
        self.publish({'kind': 'dismiss', 'device_id': device, 'notification_id': identifier,
                      'timestamp': int(time.time())})
        self.dedupe.seen.pop((device, identifier), None)
        if self.current and self.current[:2] == (device, identifier):
            self.current = None

    def scan_apps(self, device):
        path = f'/modules/kdeconnect/devices/{device}/notifications'
        result = self.bus.call_sync(SERVICE, path, NOTIFICATIONS, 'activeNotifications', None,
                                   None, self.Gio.DBusCallFlags.NONE, 2000, None)
        for identifier in result.unpack()[0][:128]:
            try:
                self.preferences.observe(self.properties(path + '/' + identifier).get('appName', ''))
            except Exception:
                pass  # Notifications can disappear during discovery.

    def reconcile_policy(self):
        if self.current and not self.preferences.allows(self.current[2], self.current[3]):
            self.dismiss(*self.current[:2])

    def process_job(self, kind, device, identifier):
        if kind == 'policy':
            self.reconcile_policy()
            return
        if kind == 'scan':
            self.scan_apps(device)
            return
        if kind == 'remove':
            self.dismiss(device, identifier)
            return
        if kind == 'clear':
            self.publish({'kind': 'dismiss_device', 'device_id': device, 'timestamp': int(time.time())})
            for key in [k for k in self.dedupe.seen if k[0] == device]:
                self.dedupe.seen.pop(key, None)
            if self.current and self.current[0] == device:
                self.current = None
            return
        props = self.properties(f'/modules/kdeconnect/devices/{device}/notifications/{identifier}')
        app = props.get('appName', '')
        self.preferences.observe(app)
        self.reconcile_policy()
        if not self.preferences.allows(app, props.get('silent', False)):
            return
        choices = self.preferences.snapshot()
        # App choices and silent filtering are managed by Preferences, not the initial TOML.
        payload = notification(props, device, identifier, {'ignore_silent': False,
                               'timeout_seconds': choices['timeout_seconds']})
        if payload is None:
            return
        image = None
        if self.config.get('images', {}).get('enabled', True) and props.get('hasIcon'):
            image = self.images.read(props.get('iconPath', ''))
        digest = hashlib.sha256(image).hexdigest() if image else ''
        key = (device, identifier)
        previous = self.dedupe.seen.get(key)
        if self.dedupe.duplicate(key, payload, digest):
            return
        if image:
            payload['image_url'] = self.images.put(image)
        try:
            self.publish(payload)
        except Exception:
            self.dedupe.seen.pop(key, None)
            if previous is not None:
                self.dedupe.seen[key] = previous
            raise
        self.current = (device, identifier, app, props.get('silent', False))
        LOG.info('Forwarded notification (image=%s)', bool(image))

    def worker(self):
        while not self.stop.is_set():
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.process_job(*job)
            except Exception as exc:
                LOG.warning('Notification event could not be forwarded (%s)', type(exc).__name__)

    def start_http(self):
        config = self.config.get('images', {})
        if not config.get('enabled', True):
            return
        store = self.images
        allowed = set(config.get('allowed_clients', []))
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if allowed and self.client_address[0] not in allowed:
                    self.send_error(403)
                    return
                data = store.get(self.path)
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass  # URL tokens and notification images must not enter logs.

            def setup(self):
                super().setup()
                self.connection.settimeout(5)
        self.http = ThreadingHTTPServer((config.get('bind', '127.0.0.1'), config.get('port', 18767)), Handler)
        self.http.daemon_threads = True
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def shutdown(self, *_):
        self.stop.set()
        self.loop.quit()

    def run(self):
        self.start_http()
        self.settings_http = start_settings(self.preferences, self.policy_changed,
                                           self.config.get('settings', {}).get('port', 18769))
        LOG.info('Settings: http://127.0.0.1:%s', self.config.get('settings', {}).get('port', 18769))
        subscriptions = []
        for interface in (NOTIFICATIONS, ITEM):
            subscriptions.append(self.bus.signal_subscribe(SERVICE, interface, None, None, None,
                self.Gio.DBusSignalFlags.NONE, self.on_signal, None))
        self.bus.connect('closed', self.shutdown)
        for device in self.config['device_ids']:
            self.enqueue(device, '', 'scan')
        thread = threading.Thread(target=self.worker, daemon=True)
        thread.start()
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        LOG.info('Listening for new KDE Connect notifications; existing notifications are not replayed')
        try:
            self.loop.run()
        finally:
            self.stop.set()
            for subscription in subscriptions:
                self.bus.signal_unsubscribe(subscription)
            thread.join(timeout=8)
            if self.http:
                self.http.shutdown()
                self.http.server_close()
            if self.settings_http:
                self.settings_http.shutdown()
                self.settings_http.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path.home() / '.config/kdeconnect-knob-bridge/config.toml'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    config = load_config(args.config)
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    Bridge(config, Gio, GLib, Path(args.config).with_name('preferences.json')).run()


if __name__ == '__main__':
    main()

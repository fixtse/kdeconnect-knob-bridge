"""Notification formatting, filtering, and bounded in-memory image storage."""
from collections import OrderedDict
import hashlib
import json
import secrets
import struct
import threading
import time
from pathlib import Path


def clip(text, limit):
    return str(text or '').encode('utf-8')[:limit].decode('utf-8', errors='ignore')


def notification(properties, device, notification_id, config):
    app = str(properties.get('appName') or '')
    if config.get('include_apps') and app not in config['include_apps']:
        return None
    if app in config.get('exclude_apps', []):
        return None
    if config.get('ignore_silent', True) and properties.get('silent', False):
        return None
    title = str(properties.get('title') or '')
    body = properties.get('text') or properties.get('ticker') or title
    if not body:
        return None
    heading = f'{app} · {title}' if app and title and app != title else title or app
    return {'kind': 'notification', 'device_id': device, 'notification_id': notification_id,
            'app': clip(app, 128), 'title': clip(heading.replace('\n', ' ').replace('\r', ' '), 96),
            'message': clip(body, 768), 'image_url': '',
            'timeout_seconds': config.get('timeout_seconds', 30), 'timestamp': int(time.time())}


class Deduplicator:
    def __init__(self, ttl=120, limit=512):
        self.ttl, self.limit = ttl, limit
        self.seen = OrderedDict()

    def duplicate(self, key, payload, image_digest=''):
        # Ignore timestamps and temporary URLs. Keep notification updates with changed content.
        fields = {k: payload[k] for k in ('title', 'message', 'app')}
        fingerprint = hashlib.sha256((json.dumps(fields, sort_keys=True) + image_digest).encode()).digest()
        now = time.monotonic()
        old = self.seen.pop(key, None)
        self.seen[key] = (fingerprint, now)
        while len(self.seen) > self.limit:
            self.seen.popitem(last=False)
        return old is not None and old[0] == fingerprint and now - old[1] < self.ttl


class ImageStore:
    def __init__(self, base_url, roots, ttl=900, limit=32, max_bytes=524288):
        self.base_url = base_url.rstrip('/')
        self.roots = [Path(p).expanduser().resolve() for p in roots]
        self.ttl, self.limit, self.max_bytes = ttl, limit, max_bytes
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def read(self, filename):
        """Only copy bounded PNG files inside explicitly configured KDE cache roots."""
        if not filename or not self.roots:
            return None
        try:
            path = Path(filename).resolve(strict=True)
            if not any(path.is_relative_to(root) for root in self.roots):
                return None
            if not path.is_file() or path.stat().st_size > self.max_bytes:
                return None
            with path.open('rb') as source:
                data = source.read(self.max_bytes + 1)
            if len(data) > self.max_bytes or len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[12:16] != b'IHDR':
                return None
            width, height = struct.unpack('>II', data[16:24])
            if not (0 < width <= 2048 and 0 < height <= 2048):
                return None
            return data
        except (OSError, ValueError):
            return None

    def _expire(self):
        now = time.monotonic()
        for key in [k for k, (_, deadline) in self.items.items() if deadline <= now]:
            del self.items[key]

    def put(self, data):
        token = secrets.token_urlsafe(24)
        with self.lock:
            self._expire()
            self.items[token] = (data, time.monotonic() + self.ttl)
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return self.base_url + '/images/' + token + '.png'

    def get(self, path):
        if not path.startswith('/images/') or not path.endswith('.png'):
            return None
        token = path[len('/images/'):-4]
        with self.lock:
            self._expire()
            found = self.items.get(token)
            return found[0] if found else None

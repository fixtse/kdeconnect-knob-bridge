"""Persist app choices, never notification content."""
import json
import os
from pathlib import Path
import tempfile
import threading


class Preferences:
    def __init__(self, path, config):
        self.path = Path(path).expanduser()
        self.lock = threading.RLock()
        self.config = config
        self.data = {'enabled': True, 'default_share': not bool(config.get('include_apps')),
                     'ignore_silent': config.get('ignore_silent', True),
                     'timeout_seconds': config.get('timeout_seconds', 30),
                     'apps': {name: True for name in config.get('include_apps', [])}, 'known_apps': []}
        self.data['apps'].update({name: False for name in config.get('exclude_apps', [])})
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            self.validate(saved)
            self.data.update(saved)

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or set(data) - {'enabled', 'default_share', 'ignore_silent', 'timeout_seconds', 'apps', 'known_apps'}:
            raise ValueError('Unknown preference fields')
        for key in ('enabled', 'default_share', 'ignore_silent'):
            if key in data and type(data[key]) is not bool:
                raise ValueError(key + ' must be a boolean')
        if 'timeout_seconds' in data and (type(data['timeout_seconds']) is not int or not 0 <= data['timeout_seconds'] <= 3600):
            raise ValueError('Choose a duration between 0 and 3600 seconds')
        apps = data.get('apps', {})
        if not isinstance(apps, dict) or len(apps) > 512 or not all(isinstance(k, str) and 0 < len(k.encode()) <= 256 and type(v) is bool for k, v in apps.items()):
            raise ValueError('Invalid app choices')
        known = data.get('known_apps', [])
        if not isinstance(known, list) or len(known) > 512 or not all(isinstance(k, str) and 0 < len(k.encode()) <= 256 for k in known):
            raise ValueError('Invalid app names')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.preferences-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def update(self, data):
        self.validate(data)
        if 'known_apps' in data:
            raise ValueError('App discovery is managed by the bridge')
        with self.lock:
            previous = self.data.copy()
            self.data = {**self.data, **data}
            try:
                self.save()
            except Exception:
                self.data = previous
                raise
        return self.snapshot()

    def observe(self, app):
        if not isinstance(app, str) or not 0 < len(app.encode()) <= 256:
            return
        with self.lock:
            if app not in self.data['known_apps'] and len(self.data['known_apps']) < 512:
                self.data['known_apps'].append(app)
                self.save()

    def allows(self, app, silent=False):
        with self.lock:
            return (self.data['enabled'] and self.data['apps'].get(app, self.data['default_share'])
                    and not (self.data['ignore_silent'] and silent))

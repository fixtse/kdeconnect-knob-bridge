"""Loopback-only settings UI, separate from the knob's LAN image endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import threading


def start_settings(preferences, on_change, port=18769):
    token = secrets.token_urlsafe(32)
    page = (Path(__file__).with_name('settings.html').read_text().replace('__TOKEN__', token)).encode()
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass

        def reply(self, code, body, content_type='application/json'):
            data = json.dumps(body).encode() if isinstance(body, dict) else body
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, token_required=True):
            if self.headers.get('Host') not in hosts:
                self.reply(403, {'error': 'Open settings using localhost or 127.0.0.1.'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://{h}' for h in hosts}:
                self.reply(403, {'error': 'Open settings on this desktop.'})
                return False
            if token_required and not hmac.compare_digest(self.headers.get('X-Bridge-Token', ''), token):
                self.reply(403, {'error': 'Refresh this page to reconnect to the bridge.'})
                return False
            return True

        def do_GET(self):
            if not self.authorized(self.path != '/'):
                return
            if self.path == '/':
                self.reply(200, page, 'text/html; charset=utf-8')
            elif self.path == '/api/state':
                self.reply(200, preferences.snapshot())
            else:
                self.reply(404, {'error': 'Page not found'})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != '/api/preferences':
                self.reply(404, {'error': 'Page not found'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Expected JSON settings')
                updated = preferences.update(json.loads(self.rfile.read(size)))
                on_change()
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
                return
            except OSError:
                self.reply(500, {'error': 'Settings could not be saved. Check the configuration folder permissions.'})
                return
            self.reply(200, updated)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

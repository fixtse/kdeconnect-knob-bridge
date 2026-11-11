import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from knob_bridge.core import Deduplicator, ImageStore, clip, notification
from knob_bridge.__main__ import Bridge, load_config


class NotificationTests(unittest.TestCase):
    def test_multibyte_limits_and_fallback(self):
        payload = notification({'appName': 'Chat', 'title': 'é'*100, 'text': '😀'*300}, 'd', '1', {})
        self.assertLessEqual(len(payload['title'].encode()), 96)
        self.assertEqual(len(payload['message'].encode()), 768)
        self.assertEqual(notification({'title': 'Only title'}, 'd', '2', {})['message'], 'Only title')
        self.assertEqual(clip('😀', 3), '')

    def test_app_and_silent_filters(self):
        p = {'appName': 'Chat', 'text': 'hello', 'silent': True}
        self.assertIsNone(notification(p, 'd', '1', {}))
        self.assertIsNotNone(notification(p, 'd', '1', {'ignore_silent': False}))
        p['silent'] = False
        self.assertIsNone(notification(p, 'd', '1', {'include_apps': ['Mail']}))
        self.assertIsNone(notification(p, 'd', '1', {'exclude_apps': ['Chat']}))
        self.assertIsNone(notification({}, 'd', '1', {}))

    def test_dedupe_content_image_changes_and_expiry(self):
        d = Deduplicator(ttl=5, limit=2)
        p = notification({'text': 'hello'}, 'd', '1', {})
        with patch('knob_bridge.core.time.monotonic', return_value=0):
            self.assertFalse(d.duplicate('a', p))
            self.assertTrue(d.duplicate('a', {**p, 'timestamp': 100}))
            self.assertFalse(d.duplicate('a', p, 'image1'))
            self.assertFalse(d.duplicate('a', {**p, 'message': 'changed'}, 'image1'))
        with patch('knob_bridge.core.time.monotonic', return_value=6):
            self.assertFalse(d.duplicate('a', p))
        d.duplicate('b', p); d.duplicate('c', p)
        self.assertEqual(len(d.seen), 2)


class ImageTests(unittest.TestCase):
    def test_restrict_files_png_size_and_expiring_urls(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            store = ImageStore('http://desktop:18767', [root], ttl=5, limit=1, max_bytes=100)
            data = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 112, 64) + bytes(9)
            p = Path(root)/'image'; p.write_bytes(data)
            self.assertEqual(store.read(p), data)
            q = Path(outside)/'secret'; q.write_bytes(data)
            (Path(root)/'link').symlink_to(q)
            self.assertIsNone(store.read(q)); self.assertIsNone(store.read(Path(root)/'link'))
            p.write_bytes(b'x'*101); self.assertIsNone(store.read(p))
            p.write_bytes(data[:16]+struct.pack('>II', 9999, 64)+data[24:]); self.assertIsNone(store.read(p))
            with patch('knob_bridge.core.time.monotonic', return_value=0):
                url = store.put(data); path = url.split(':18767')[1]
                self.assertEqual(store.get(path), data)
                new = store.put(data)
                self.assertIsNone(store.get(path))
            with patch('knob_bridge.core.time.monotonic', return_value=6):
                self.assertIsNone(store.get(new.split(':18767')[1]))
            self.assertIsNone(store.get('/../../secret'))


class ConfigAndRoutingTests(unittest.TestCase):
    def test_example_config_and_retain_rejection(self):
        example = Path('examples/config.toml').read_text()
        with tempfile.NamedTemporaryFile('w+', suffix='.toml') as f:
            f.write(example); f.flush()
            self.assertEqual(load_config(f.name)['timeout_seconds'], 30)
            f.seek(0); f.truncate(); f.write(example.replace('"mosquitto_pub", "-h"', '"mosquitto_pub", "-r", "-h"')); f.flush()
            with self.assertRaises(ValueError): load_config(f.name)

    def test_notification_and_ready_signal_routing(self):
        from unittest.mock import Mock
        bridge = object.__new__(Bridge)
        bridge.enqueue = Mock()
        parameters = Mock(); parameters.unpack.return_value = ('19',)
        path = '/modules/kdeconnect/devices/phone/notifications'
        bridge.on_signal(None, None, path, None, 'notificationPosted', parameters, None)
        bridge.on_signal(None, None, path+'/19', None, 'ready', parameters, None)
        bridge.on_signal(None, None, path, None, 'notificationRemoved', parameters, None)
        self.assertEqual(bridge.enqueue.call_count, 3)
        bridge.enqueue.assert_called_with('phone', '19', 'remove')

    def test_publish_uses_stdin_and_no_retention(self):
        bridge = object.__new__(Bridge)
        bridge.config = {'mqtt': {'command': ['mosquitto_pub', '-h', 'broker'], 'topic': 'knob/events'}}
        with patch('knob_bridge.__main__.subprocess.run') as run:
            bridge.publish({'message': 'private'})
            args, kwargs = run.call_args
            self.assertNotIn('-r', args[0]); self.assertNotIn('private', args[0])
            self.assertEqual(json.loads(kwargs['input']), {'message': 'private'})


if __name__ == '__main__':
    unittest.main()

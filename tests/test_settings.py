import json
from pathlib import Path
import queue
import re
import tempfile
import unittest
from unittest.mock import Mock
import urllib.request
import urllib.error
from knob_bridge.preferences import Preferences
from knob_bridge.__main__ import Bridge
from knob_bridge.core import Deduplicator
from knob_bridge.settings import start_settings


class PreferenceTests(unittest.TestCase):
    def test_save_reload_explicit_choices_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'preferences.json'
            prefs = Preferences(p, {'exclude_apps':['Noise']})
            prefs.observe('Chat')
            self.assertTrue(prefs.allows('Chat')); self.assertFalse(prefs.allows('Noise'))
            prefs.update({'default_share':False, 'apps': {'Chat':True}, 'timeout_seconds':0})
            self.assertTrue(prefs.allows('Chat')); self.assertFalse(prefs.allows('Other'))
            self.assertFalse(prefs.allows('Chat', True))
            prefs = Preferences(p,{})
            self.assertEqual(prefs.snapshot()['known_apps'],['Chat'])
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(prefs.snapshot()['timeout_seconds'],0)
            with self.assertRaises(ValueError): prefs.update({'enabled':'yes'})
            with self.assertRaises(ValueError): prefs.update({'known_apps':['Spoof']})
            self.assertTrue(prefs.snapshot()['enabled'])

    def test_disabled_sharing_overrides_allowlist(self):
        with tempfile.TemporaryDirectory() as root:
            prefs=Preferences(Path(root)/'p.json',{'include_apps':['Chat']})
            self.assertTrue(prefs.allows('Chat')); self.assertFalse(prefs.allows('Other'))
            prefs.update({'enabled':False})
            self.assertFalse(prefs.allows('Chat'))


class SyncTests(unittest.TestCase):
    def bridge(self, root):
        b=object.__new__(Bridge)
        b.config={'images':{'enabled':False}}
        b.preferences=Preferences(Path(root)/'p.json',{})
        b.current=None; b.dedupe=Deduplicator(); b.publish=Mock()
        b.properties=Mock(return_value={'appName':'Chat','title':'Hello','text':'Message'})
        return b

    def test_removals_publish_identity_and_do_not_forget_newer_popup(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bridge(root)
            b.process_job('update','phone','1');b.process_job('update','phone','2')
            b.process_job('remove','phone','1')
            self.assertEqual(b.current[:2],('phone','2'))
            self.assertEqual(b.publish.call_args.args[0]['notification_id'],'1')
            self.assertEqual(b.publish.call_args.args[0]['kind'],'dismiss')
            b.process_job('remove','phone','2');self.assertIsNone(b.current)

    def test_policy_change_dismisses_blocked_current_and_future_is_filtered(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bridge(root);b.process_job('update','phone','1')
            b.preferences.update({'apps':{'Chat':False}})
            b.process_job('policy','','')
            self.assertEqual(b.publish.call_args.args[0]['kind'],'dismiss')
            b.publish.reset_mock();b.process_job('update','phone','2');b.publish.assert_not_called()

    def test_failed_publish_is_retryable_and_clear_matches_device(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bridge(root);b.publish.side_effect=RuntimeError('offline')
            with self.assertRaises(RuntimeError):b.process_job('update','phone','1')
            self.assertEqual(len(b.dedupe.seen),0)
            b.publish.side_effect=None;b.process_job('update','phone','1')
            b.process_job('clear','other','');self.assertIsNotNone(b.current)
            b.process_job('clear','phone','');self.assertIsNone(b.current)
            self.assertEqual(b.publish.call_args.args[0]['kind'],'dismiss_device')

    def test_clear_signal_routes_without_notification_id(self):
        b=object.__new__(Bridge);b.enqueue=Mock();p=Mock();p.unpack.return_value=()
        b.on_signal(None,None,'/modules/kdeconnect/devices/phone/notifications',None,'allNotificationsRemoved',p,None)
        b.enqueue.assert_called_once_with('phone','','clear')


class SettingsHTTPTests(unittest.TestCase):
    def test_ui_access_token_origin_and_persistent_save(self):
        with tempfile.TemporaryDirectory() as root:
            prefs=Preferences(Path(root)/'p.json',{})
            # Reserve a test-only loopback port dynamically.
            import socket
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            changed=Mock();server=start_settings(prefs,changed,port)
            base=f'http://127.0.0.1:{port}'
            def request(path,body=None,headers=None):
                req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers or {})
                return urllib.request.urlopen(req,timeout=3)
            try:
                with request('/') as response:html=response.read().decode()
                token=re.search("const token='([^']+)'",html)[1]
                with self.assertRaises(urllib.error.HTTPError) as missing:request('/api/state')
                self.assertEqual(missing.exception.code,403); missing.exception.close()
                headers={'X-Bridge-Token':token,'Content-Type':'application/json'}
                with self.assertRaises(urllib.error.HTTPError) as cross:request('/api/preferences',{'enabled':False},{**headers,'Origin':'https://example.org'})
                self.assertEqual(cross.exception.code,403); cross.exception.close()
                with request('/api/preferences',{'enabled':False},headers) as response:
                    self.assertFalse(json.load(response)['enabled'])
                self.assertEqual(changed.call_count,1)
                self.assertFalse(Preferences(Path(root)/'p.json',{}).snapshot()['enabled'])
                with self.assertRaises(urllib.error.HTTPError) as bad:request('/api/preferences',{'enabled':'yes'},headers)
                self.assertEqual(bad.exception.code,400); bad.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as host:request('/',headers={'Host':f'evil.example:{port}'})
                self.assertEqual(host.exception.code,403); host.exception.close()
            finally:
                server.shutdown();server.server_close()

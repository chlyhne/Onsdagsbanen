"""Local address aliases work without accepting foreign request origins."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from onsdagsbane import auth
from onsdagsbane.app import Application


class PreviewOriginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Application(db_path=Path(temporary.name)/'test.sqlite3',
            base_url='http://127.0.0.1:8090', initialize=False, preview_admin=True)
        self.token = 'preview-session-for-origin-test'
        self.app.preview_sessions[auth.digest(self.token)] = {'csrf': 'test-csrf', 'expires': time.time()+600}

    def post(self, origin, host, *, remote='127.0.0.1', scheme='http', csrf='test-csrf'):
        # A missing endpoint proves that origin and CSRF checks both passed,
        # without making any changes or invalidating the test session.
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/no-such-operation',
            'HTTP_ORIGIN': origin, 'HTTP_HOST': host, 'wsgi.url_scheme': scheme,
            'REMOTE_ADDR': remote, 'HTTP_COOKIE': 'ob_preview='+self.token,
            'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(b'{}')}
        status = []
        body = b''.join(self.app(env, lambda s, h: status.append(int(s[:3]))))
        return status[0], json.loads(body)

    def test_preview_accepts_matching_loopback_host_and_port(self):
        for host, remote in (('localhost:8090', '127.0.0.1'), ('127.0.0.1:8090', '127.0.0.1'), ('[::1]:8090', '::1')):
            with self.subTest(host=host):
                self.assertEqual(self.post('http://'+host, host, remote=remote)[0], 404)
        self.app.base_url = 'http://localhost:8090'
        self.assertEqual(self.post('http://127.0.0.1:8090', '127.0.0.1:8090')[0], 404)
        self.assertEqual(self.post('http://localhost:8090', 'localhost:8090', csrf='wrong')[0], 403)

    def test_preview_rejects_other_origins_ports_hosts_and_remote_clients(self):
        attempts = [
            ('https://evil.example', 'localhost:8090', {}),
            ('http://localhost.evil.example:8090', 'localhost.evil.example:8090', {}),
            ('http://localhost:8091', 'localhost:8091', {}),
            ('http://localhost:8090', '127.0.0.1:8090', {}),
            ('http://localhost:8090', 'localhost:8090', {'remote': '203.0.113.2'}),
            ('https://localhost:8090', 'localhost:8090', {'scheme': 'https'}),
            ('null', 'localhost:8090', {}),
        ]
        for origin, host, options in attempts:
            with self.subTest(origin=origin, host=host, options=options):
                status, body = self.post(origin, host, **options)
                self.assertEqual(status, 403)
                self.assertEqual(body['error'], 'Ugyldig forespørgselsoprindelse.')

    def test_normal_server_and_public_base_url_keep_exact_origin_check(self):
        self.app.preview_admin = False
        self.assertEqual(self.post('http://localhost:8090', 'localhost:8090')[0], 403)
        self.app.preview_admin = True
        self.app.base_url = 'https://onsdagsbanen.example'
        self.assertEqual(self.post('http://localhost:8090', 'localhost:8090')[0], 403)
        self.assertEqual(self.post('https://onsdagsbanen.example', 'onsdagsbanen.example', scheme='https')[0], 404)

    def test_demo_supports_aliases_and_default_port(self):
        self.app.preview_admin = False
        self.app.demo = True
        self.app.base_url = 'http://127.0.0.1'
        self.assertEqual(self.post('http://localhost', 'localhost', csrf='local-demo')[0], 404)
        self.assertEqual(self.post('http://localhost:80', 'localhost:80', csrf='local-demo')[0], 404)

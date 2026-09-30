import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evaluation.smoke_local import run


class HTTPTests(unittest.TestCase):
    def test_live_contract_auth_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            env = dict(os.environ, MEMORY_DB_PATH=tmp + '/db.sqlite3', MEMORY_API_KEY='test-only-credential')
            def start():
                process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'service.app:app',
                                            '--host', '127.0.0.1', '--port', str(port)],
                                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(100):
                    try:
                        with urlopen(base + '/health', timeout=1) as response:
                            if response.status == 200:
                                return process
                    except (URLError, OSError):
                        if process.poll() is not None:
                            self.fail('service startup failed')
                        time.sleep(.05)
                process.terminate()
                process.wait(timeout=5)
                self.fail('service startup timeout')
            def call(path, payload, headers):
                request = Request(base + path, data=json.dumps(payload).encode(),
                                  headers={'Content-Type': 'application/json', **headers})
                with urlopen(request, timeout=5) as response:
                    return json.load(response)
            process = start()
            try:
                report = run(base, 'test-only-credential')
                query = {'query': 'Hangzhou', 'user_id': report['user_id'], 'top_k': 100}
                with self.assertRaises(HTTPError) as caught:
                    call('/search', query, {})
                self.assertEqual(caught.exception.code, 401)
                expected = call('/search', query, {'X-Api-Key': 'test-only-credential'})
                self.assertEqual(expected, call('/search', query, {'Authorization': 'Token test-only-credential'}))
                process.terminate()
                process.wait(timeout=5)
                process = start()
                self.assertEqual(expected, call('/search', query, {'Authorization': 'Bearer test-only-credential'}))
            finally:
                process.terminate()
                process.wait(timeout=5)

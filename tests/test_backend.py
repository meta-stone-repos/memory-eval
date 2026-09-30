import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backends.sqlite_bm25 import SQLiteBM25, RequestConflict


class BackendTests(unittest.TestCase):
    def test_restart_concurrent_retries_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory.db'
            backend = SQLiteBM25(path)
            payload = dict(user_id='u', session_id='s', request_id='r', messages=[
                dict(role='user', content='Hangzhou'), dict(role='assistant', content='West Lake')])
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: backend.add(payload), range(16)))
            before = backend.search('u', 'Hangzhou', 100)
            self.assertEqual(len(before), 1)
            restarted = SQLiteBM25(path)
            self.assertEqual(before, restarted.search('u', 'Hangzhou', 100))
            with restarted.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 2)
                self.assertEqual([r[0] for r in db.execute('SELECT content FROM memories ORDER BY rowid')],
                                 ['Hangzhou', 'West Lake'])
            with self.assertRaises(RequestConflict):
                restarted.add(dict(payload, session_id='different'))
            self.assertEqual(restarted.search('other', 'Hangzhou', 100), [])


if __name__ == '__main__':
    unittest.main()


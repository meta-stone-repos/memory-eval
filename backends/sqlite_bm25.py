import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class RequestConflict(Exception):
    pass


def tokens(text):
    # English words and overlapping CJK bigrams; preserve single CJK characters.
    parts = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.lower())
    result = []
    for part in parts:
        if re.match(r"[\u3400-\u9fff]", part):
            result.extend(part)
            result.extend(part[i:i + 2] for i in range(len(part) - 1))
        else:
            result.append(part)
    return result


class SQLiteBM25:
    """Small baseline: durable source messages, user-scoped in-memory BM25 scoring."""
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS requests (
                    user_id TEXT, request_id TEXT, digest TEXT NOT NULL,
                    PRIMARY KEY(user_id, request_id));
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, timestamp INTEGER,
                    created_at TEXT NOT NULL, terms TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, payload):
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT digest FROM requests WHERE user_id=? AND request_id=?",
                                  (payload['user_id'], payload['request_id'])).fetchone()
            if existing:
                if existing['digest'] != digest:
                    raise RequestConflict("request_id already exists with different content")
                return
            for index, msg in enumerate(payload['messages']):
                identity = json.dumps([payload['user_id'], payload['request_id'], index])
                mid = "mem_" + hashlib.sha256(identity.encode()).hexdigest()
                timestamp = msg.get('timestamp')
                created = datetime.now(timezone.utc) if timestamp is None else datetime.fromtimestamp(timestamp / 1000, timezone.utc)
                db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?)",
                           (mid, payload['user_id'], payload['session_id'], msg['role'], msg['content'],
                            timestamp, created.isoformat().replace('+00:00', 'Z'),
                            json.dumps(tokens(msg['content']), ensure_ascii=False)))
            db.execute("INSERT INTO requests VALUES (?,?,?)", (payload['user_id'], payload['request_id'], digest))

    def search(self, user_id, query, top_k):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM memories WHERE user_id=? ORDER BY rowid", (user_id,)).fetchall()
        if not rows:
            return []
        docs = [Counter(json.loads(row['terms'])) for row in rows]
        lengths = [sum(doc.values()) for doc in docs]
        avg = sum(lengths) / len(lengths) or 1
        terms = set(tokens(query))
        frequencies = {term: sum(term in doc for doc in docs) for term in terms}
        ranked = []
        for row, doc, length in zip(rows, docs, lengths):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    df = frequencies[term]
                    idf = math.log(1 + (len(rows) - df + .5) / (df + .5))
                    score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * length / avg))
            if score > 0:
                ranked.append(dict(id=row['id'], content=row['content'], score=score, created_at=row['created_at']))
        return sorted(ranked, key=lambda item: (-item['score'], item['id']))[:top_k]

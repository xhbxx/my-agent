"""SQLite approval state + minimal audit data; no result rows or SQL literals.

Confirmation is short-lived, bound to principal/source/SQL/policy and consumed
atomically. Workers may share this SQLite file on a single host.
"""

import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .contracts import AnalysisError


class AuditStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS analysis_approval ("
                "id TEXT PRIMARY KEY, principal TEXT, source TEXT, digest TEXT, "
                "policy TEXT, expires REAL, consumed INTEGER DEFAULT 0);"
                "CREATE TABLE IF NOT EXISTS analysis_audit ("
                "request_id TEXT, principal TEXT, source TEXT, digest TEXT, "
                "stage TEXT, row_count INTEGER, elapsed_ms REAL, created REAL);"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def issue(self, principal, policy, query):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM analysis_approval WHERE expires < ?", (now,))
            db.execute(
                "INSERT INTO analysis_approval VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    token,
                    principal.name,
                    policy.name,
                    query.digest,
                    policy.version,
                    now + 300,
                ),
            )
        return token

    def consume(self, token, principal, policy, query):
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "UPDATE analysis_approval SET consumed=1 WHERE id=? AND principal=? "
                "AND source=? AND digest=? AND policy=? AND expires>? AND consumed=0",
                (
                    token,
                    principal.name,
                    policy.name,
                    query.digest,
                    policy.version,
                    time.time(),
                ),
            )
            if cursor.rowcount != 1:
                raise AnalysisError("确认已失效、已使用或与当前用户/SQL/策略不一致")

    def record(self, request_id, principal, source, digest, stage, result=None):
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO analysis_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    principal.name,
                    source,
                    digest,
                    stage,
                    len(result.rows) if result else 0,
                    result.elapsed_ms if result else 0,
                    time.time(),
                ),
            )

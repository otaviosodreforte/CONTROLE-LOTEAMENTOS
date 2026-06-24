import os
import sys
from functools import wraps
from flask import session, redirect, url_for

if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
else:
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH = os.environ.get("DB_PATH") or os.path.join(_base, "controle_loteamentos.db")
RECIBOS_DIR = os.environ.get("RECIBOS_DIR") or os.path.join(_base, "recibos")

os.makedirs(RECIBOS_DIR, exist_ok=True)

USING_PG = bool(DATABASE_URL)


if USING_PG:
    import psycopg2
    import psycopg2.extras

    class _PgConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=None):
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql.replace("?", "%s"), params or ())
            return cur

        def executescript(self, sql):
            cur = self._conn.cursor()
            for stmt in sql.split(";"):
                s = stmt.strip()
                if s:
                    cur.execute(s)
            cur.close()

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            self._conn.close()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                try:
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
            else:
                self._conn.rollback()
            self._conn.close()

    def get_db():
        return _PgConnection(psycopg2.connect(DATABASE_URL))

else:
    import sqlite3

    def get_db():
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def permissao_required(modulo):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if modulo not in session.get("permissoes", []):
                flash("Acesso negado.")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated
    return decorator

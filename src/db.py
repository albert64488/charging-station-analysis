"""SQLite 스키마 및 저장 헬퍼 (이벤트 기반 v2).

- stations        : 충전소 메타 (위치/사업자)
- chargers        : 충전기 메타 (타입/출력/급속여부)
- current_state   : 각 충전기의 현재 상태 + 진입시각 (열린 구간)
- state_intervals : 상태가 바뀔 때 닫힌 구간 기록 (시간 기반 이용률의 원천)
"""
import os
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    stat_id     TEXT PRIMARY KEY,
    stat_nm     TEXT,
    addr        TEXT,
    lat         REAL,
    lng         REAL,
    busi_id     TEXT,
    busi_nm     TEXT,
    zcode       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS chargers (
    charger_key TEXT PRIMARY KEY,   -- statId-chgerId
    stat_id     TEXT,
    chger_id    TEXT,
    chger_type  TEXT,
    is_fast     INTEGER,            -- 1 급속 / 0 완속
    output      REAL,               -- kW
    busi_nm     TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS current_state (
    charger_key TEXT PRIMARY KEY,
    stat        INTEGER,
    since_dt    TEXT,               -- 현재 상태 진입 시각 (ISO)
    stat_upd_dt TEXT,               -- 원본 statUpdDt(ISO)
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS state_intervals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    charger_key TEXT,
    stat        INTEGER,
    start_dt    TEXT,
    end_dt      TEXT
);

CREATE INDEX IF NOT EXISTS idx_chargers_stat ON chargers(stat_id);
CREATE INDEX IF NOT EXISTS idx_si_charger    ON state_intervals(charger_key);
CREATE INDEX IF NOT EXISTS idx_si_start      ON state_intervals(start_dt);
CREATE INDEX IF NOT EXISTS idx_si_end        ON state_intervals(end_dt);
"""


def _apply_schema(conn):
    """SQLite/libSQL 공통: 스키마 문장을 개별 실행."""
    for stmt in SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


# --- Turso(libsql-client) 어댑터: sqlite3.Connection처럼 보이게 감싼다 ---

def _turso_url():
    u = config.TURSO_DATABASE_URL
    if u.startswith("libsql://"):
        u = "https://" + u[len("libsql://"):]
    return u


class _TursoCursor:
    def __init__(self, rs):
        self._rows = [tuple(r) for r in rs.rows]
        self.description = [(c,) for c in rs.columns] if rs.columns else None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _TursoConn:
    """db.py가 쓰는 execute/executemany/commit 인터페이스만 구현."""

    BATCH = 1000

    def __init__(self, client):
        self._c = client

    def execute(self, sql, params=()):
        return _TursoCursor(self._c.execute(sql, list(params)))

    def executemany(self, sql, rows):
        rows = list(rows)
        for i in range(0, len(rows), self.BATCH):
            self._c.batch([(sql, list(r)) for r in rows[i:i + self.BATCH]])

    def executescript(self, script):
        for stmt in script.split(";"):
            s = stmt.strip()
            if s:
                self._c.execute(s)

    def commit(self):
        pass  # libsql-client는 자동 커밋

    def sync(self):
        pass

    def close(self):
        self._c.close()


def init_db(path=None):
    with get_conn(path):
        pass


@contextmanager
def get_conn(path=None):
    # Turso(클라우드) 설정 시 호스팅 SQLite 사용, 아니면 로컬 파일
    if config.TURSO_DATABASE_URL:
        import libsql_client
        client = libsql_client.create_client_sync(
            url=_turso_url(), auth_token=config.TURSO_AUTH_TOKEN)
        conn = _TursoConn(client)
        _apply_schema(conn)
        try:
            yield conn
        finally:
            conn.close()
        return

    path = path or config.DB_PATH
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path)
    _apply_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def fetch_df(conn, sql, params=()):
    """DB-드라이버 무관하게 SELECT 결과를 DataFrame으로 (sqlite3/libSQL 공용)."""
    import pandas as pd
    cur = conn.execute(sql, tuple(params))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    return pd.DataFrame([tuple(r) for r in rows], columns=cols)


# --- 배치 upsert ---

def upsert_stations(conn, rows):
    """rows: (stat_id, stat_nm, addr, lat, lng, busi_id, busi_nm, zcode, updated_at)"""
    conn.executemany(
        """
        INSERT INTO stations (stat_id, stat_nm, addr, lat, lng, busi_id, busi_nm, zcode, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stat_id) DO UPDATE SET
            stat_nm=excluded.stat_nm, addr=excluded.addr, lat=excluded.lat, lng=excluded.lng,
            busi_id=excluded.busi_id, busi_nm=excluded.busi_nm, zcode=excluded.zcode,
            updated_at=excluded.updated_at
        """,
        rows,
    )


def upsert_chargers(conn, rows):
    """rows: (charger_key, stat_id, chger_id, chger_type, is_fast, output, busi_nm, updated_at)"""
    conn.executemany(
        """
        INSERT INTO chargers (charger_key, stat_id, chger_id, chger_type, is_fast, output, busi_nm, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(charger_key) DO UPDATE SET
            chger_type=excluded.chger_type, is_fast=excluded.is_fast, output=excluded.output,
            busi_nm=excluded.busi_nm, updated_at=excluded.updated_at
        """,
        rows,
    )


# --- 이벤트(상태구간) ---

def _chunked_in(conn, sql_tmpl, keys, chunk=2000):
    """keys를 chunk 단위 IN 절로 나눠 조회. sql_tmpl에 {ph} 자리표시자."""
    rows = []
    keys = list(keys)
    for i in range(0, len(keys), chunk):
        part = keys[i:i + chunk]
        ph = ",".join("?" * len(part))
        rows.extend(conn.execute(sql_tmpl.format(ph=ph), part).fetchall())
    return rows


def load_current_states(conn, keys=None):
    """{charger_key: (stat, since_dt)}. keys 주면 해당 키만(변경분 폴링용)."""
    if keys is None:
        rows = conn.execute("SELECT charger_key, stat, since_dt FROM current_state").fetchall()
    else:
        rows = _chunked_in(
            conn, "SELECT charger_key, stat, since_dt FROM current_state WHERE charger_key IN ({ph})", keys)
    return {r[0]: (r[1], r[2]) for r in rows}


def known_charger_keys(conn, keys=None):
    if keys is None:
        return {r[0] for r in conn.execute("SELECT charger_key FROM chargers")}
    return {r[0] for r in _chunked_in(
        conn, "SELECT charger_key FROM chargers WHERE charger_key IN ({ph})", keys)}


def known_station_ids(conn):
    return {r[0] for r in conn.execute("SELECT stat_id FROM stations")}


def insert_intervals(conn, rows):
    """rows: (charger_key, stat, start_dt, end_dt)"""
    conn.executemany(
        "INSERT INTO state_intervals (charger_key, stat, start_dt, end_dt) VALUES (?,?,?,?)",
        rows,
    )


def upsert_current_states(conn, rows):
    """rows: (charger_key, stat, since_dt, stat_upd_dt, updated_at)"""
    conn.executemany(
        """
        INSERT INTO current_state (charger_key, stat, since_dt, stat_upd_dt, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(charger_key) DO UPDATE SET
            stat=excluded.stat, since_dt=excluded.since_dt,
            stat_upd_dt=excluded.stat_upd_dt, updated_at=excluded.updated_at
        """,
        rows,
    )


def stats(conn):
    def one(q):
        return conn.execute(q).fetchone()[0]
    return {
        "stations": one("SELECT COUNT(*) FROM stations"),
        "chargers": one("SELECT COUNT(*) FROM chargers"),
        "current_state": one("SELECT COUNT(*) FROM current_state"),
        "intervals": one("SELECT COUNT(*) FROM state_intervals"),
    }

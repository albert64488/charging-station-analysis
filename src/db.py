"""SQLite 스키마 및 저장 헬퍼 (집계 기반 v3).

- stations      : 충전소 메타 (위치/사업자)
- chargers      : 충전기 메타 (타입/출력/급속여부)
- current_state : 각 충전기의 현재 상태 + 진입시각 (열린 구간)
- charger_stats : 충전기별 누적 충전/장애 시간(초) 카운터 (저장량 고정)
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
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
    kind        TEXT,               -- 충전소 구분 대분류 (API kind)
    kind_detail TEXT,               -- 충전소 구분 상세 (API kindDetail)
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

-- 집계 저장: 충전기별 누적 충전/장애 시간(초). 514k행 고정, 안 늘어남.
CREATE TABLE IF NOT EXISTS charger_stats (
    charger_key  TEXT PRIMARY KEY,
    charging_sec REAL DEFAULT 0,
    fault_sec    REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_chargers_stat ON chargers(stat_id);
"""


def _ensure_column(conn, table, col, coltype):
    """기존 테이블에 컬럼이 없으면 추가 (SQLite 마이그레이션용)."""
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    except Exception:
        pass  # 스키마 조회 실패해도 앱/수집이 막히지 않게


def _apply_schema(conn):
    """스키마 문장 개별 실행 (Postgres는 AUTOINCREMENT→BIGSERIAL 변환)."""
    schema = SCHEMA
    if isinstance(conn, _PgConn):
        schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    for stmt in schema.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    # 기존 DB(구 스키마)에 유형 컬럼 백필 마이그레이션
    _ensure_column(conn, "stations", "kind", "TEXT")
    _ensure_column(conn, "stations", "kind_detail", "TEXT")


# --- Turso(libsql-client) 어댑터: sqlite3.Connection처럼 보이게 감싼다 ---

def _turso_url():
    u = config.TURSO_DATABASE_URL
    if u.startswith("libsql://"):
        u = "https://" + u[len("libsql://"):]
    return u


class _TursoCursor:
    def __init__(self, cols, rows):
        self._rows = [tuple(r) for r in rows]
        self.description = [(c,) for c in cols] if cols else None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _TursoHTTP:
    """Turso HTTP API(v2/pipeline) 직접 호출 — libsql-client 호환성 문제 회피."""

    def __init__(self, base_url, token):
        self.url = base_url.rstrip("/") + "/v2/pipeline"
        self.token = token

    @staticmethod
    def _to_arg(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool):
            return {"type": "integer", "value": str(int(v))}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        return {"type": "text", "value": str(v)}

    @staticmethod
    def _from_cell(cell):
        t = cell.get("type")
        if t == "null":
            return None
        val = cell.get("value")
        if t == "integer":
            return int(val)
        if t == "float":
            return float(val)
        return val

    def _pipeline(self, exec_requests):
        body = json.dumps({"requests": exec_requests + [{"type": "close"}]}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        for res in data.get("results", []):
            if res.get("type") != "ok":
                raise RuntimeError(f"Turso error: {res.get('error')}")
        return data["results"]

    def execute(self, sql, params):
        reqs = [{"type": "execute",
                 "stmt": {"sql": sql, "args": [self._to_arg(p) for p in params]}}]
        result = self._pipeline(reqs)[0]["response"]["result"]
        cols = [c["name"] for c in result["cols"]]
        rows = [[self._from_cell(c) for c in row] for row in result["rows"]]
        return cols, rows

    def batch(self, statements):
        reqs = [{"type": "execute",
                 "stmt": {"sql": sql, "args": [self._to_arg(p) for p in params]}}
                for sql, params in statements]
        self._pipeline(reqs)


class _TursoConn:
    """db.py가 쓰는 execute/executemany/commit 인터페이스만 구현."""

    BATCH = 1000

    def __init__(self, client):
        self._c = client

    def _retry(self, fn):
        """일시 오류(5xx/timeout/연결)에 재시도."""
        for attempt in range(config.MAX_RETRIES):
            try:
                return fn()
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < config.MAX_RETRIES - 1:
                    time.sleep(config.RETRY_BACKOFF * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < config.MAX_RETRIES - 1:
                    time.sleep(config.RETRY_BACKOFF * (attempt + 1))
                    continue
                raise

    def execute(self, sql, params=()):
        cols, rows = self._retry(lambda: self._c.execute(sql, list(params)))
        return _TursoCursor(cols, rows)

    def executemany(self, sql, rows):
        rows = list(rows)
        for i in range(0, len(rows), self.BATCH):
            chunk = rows[i:i + self.BATCH]
            self._retry(lambda c=chunk: self._c.batch([(sql, list(r)) for r in c]))

    def executescript(self, script):
        for stmt in script.split(";"):
            s = stmt.strip()
            if s:
                self.execute(s)

    def commit(self):
        pass

    def sync(self):
        pass

    def close(self):
        pass


class _PgConn:
    """psycopg(Postgres) 어댑터 — sqlite3 conn 인터페이스. ? → %s 변환."""

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _q(sql):
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(self._q(sql), tuple(params))
        return cur  # psycopg 커서: fetchall/fetchone/description/iter 지원

    def executemany(self, sql, rows):
        cur = self._conn.cursor()
        cur.executemany(self._q(sql), [tuple(r) for r in rows])
        cur.close()

    def commit(self):
        self._conn.commit()

    def sync(self):
        pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def init_db(path=None):
    with get_conn(path) as conn:
        _apply_schema(conn)
        conn.commit()


def _pg_connect():
    """Neon은 미사용 시 자동 정지(autosuspend) → 콜드 스타트 연결 실패를 재시도."""
    import psycopg
    last = None
    for attempt in range(max(config.MAX_RETRIES, 1)):
        try:
            return psycopg.connect(config.DATABASE_URL, connect_timeout=15)
        except psycopg.OperationalError as e:
            last = e
            time.sleep(min(2.0 * (attempt + 1), 6.0))
    raise last


@contextmanager
def get_conn(path=None):
    # Turso(클라우드) 설정 시 호스팅 SQLite 사용, 아니면 로컬 파일
    if config.DATABASE_URL:  # Postgres(Neon 등) 최우선
        conn = _PgConn(_pg_connect())
        try:
            yield conn
        finally:
            conn.close()
        return

    if config.TURSO_DATABASE_URL:
        # 스키마는 init_db에서만 적용 (연결마다 재적용은 불필요한 네트워크/실패지점)
        conn = _TursoConn(_TursoHTTP(_turso_url(), config.TURSO_AUTH_TOKEN))
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
    """rows: (stat_id, stat_nm, addr, lat, lng, busi_id, busi_nm, zcode, kind, kind_detail, updated_at)"""
    conn.executemany(
        """
        INSERT INTO stations
            (stat_id, stat_nm, addr, lat, lng, busi_id, busi_nm, zcode, kind, kind_detail, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stat_id) DO UPDATE SET
            stat_nm=excluded.stat_nm, addr=excluded.addr, lat=excluded.lat, lng=excluded.lng,
            busi_id=excluded.busi_id, busi_nm=excluded.busi_nm, zcode=excluded.zcode,
            kind=excluded.kind, kind_detail=excluded.kind_detail,
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


def known_station_ids(conn, ids=None):
    if ids is None:
        return {r[0] for r in conn.execute("SELECT stat_id FROM stations")}
    return {r[0] for r in _chunked_in(
        conn, "SELECT stat_id FROM stations WHERE stat_id IN ({ph})", ids)}


def load_stats(conn, keys=None):
    """{charger_key: [charging_sec, fault_sec]} 누적 카운터."""
    if keys is None:
        rows = conn.execute("SELECT charger_key, charging_sec, fault_sec FROM charger_stats").fetchall()
    else:
        rows = _chunked_in(
            conn, "SELECT charger_key, charging_sec, fault_sec FROM charger_stats WHERE charger_key IN ({ph})", keys)
    return {r[0]: [float(r[1] or 0), float(r[2] or 0)] for r in rows}


def upsert_stats(conn, rows):
    """rows: (charger_key, charging_sec, fault_sec) — 누적 총량으로 갱신."""
    conn.executemany(
        """
        INSERT INTO charger_stats (charger_key, charging_sec, fault_sec)
        VALUES (?,?,?)
        ON CONFLICT(charger_key) DO UPDATE SET
            charging_sec=excluded.charging_sec, fault_sec=excluded.fault_sec
        """,
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


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def stats(conn):
    def one(q):
        return conn.execute(q).fetchone()[0]
    return {
        "stations": one("SELECT COUNT(*) FROM stations"),
        "chargers": one("SELECT COUNT(*) FROM chargers"),
        "current_state": one("SELECT COUNT(*) FROM current_state"),
        "stats": one("SELECT COUNT(*) FROM charger_stats"),
    }

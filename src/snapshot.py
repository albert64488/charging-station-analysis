"""정적 스냅샷 리더 — 앱이 라이브 DB 대신 발행된 스냅샷 파일을 읽게 한다.

GitHub Release(고정 태그 `latest-data`)에 수집기가 올린 `snapshot.db.gz`를 내려받아
로컬에 풀고, db.get_conn()이 그 SQLite를 읽도록 config를 로컬 파일 모드로 고정한다.

- 작은 `snapshot.txt`(생성시각)를 먼저 확인해 바뀐 경우에만 29MB 본체를 재다운로드.
- 로컬 개발: 환경변수 `SNAPSHOT_DB`에 로컬 경로를 주면 다운로드 없이 그 파일을 사용.
- 앱 무결성: 스냅샷을 못 받으면 SnapshotUnavailable를 던져 앱이 안내 메시지를 띄운다.
"""
import gzip
import os
import shutil
import tempfile
import urllib.request

import config

REPO = os.environ.get("SNAPSHOT_REPO", "albert64488/charging-station-analysis")
TAG = os.environ.get("SNAPSHOT_TAG", "latest-data")
_BASE = f"https://github.com/{REPO}/releases/download/{TAG}"
GZ_URL = f"{_BASE}/snapshot.db.gz"
STAMP_URL = f"{_BASE}/snapshot.txt"

CACHE_DIR = os.environ.get("SNAPSHOT_DIR") or os.path.join(tempfile.gettempdir(), "ev_snapshot")
DB_FILE = os.path.join(CACHE_DIR, "snapshot.db")
STAMP_FILE = os.path.join(CACHE_DIR, "snapshot.txt")


class SnapshotUnavailable(Exception):
    """스냅샷을 아직 받을 수 없음 (미발행/네트워크)."""


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "ev-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _remote_stamp():
    try:
        return _get(STAMP_URL, timeout=15).decode().strip()
    except Exception:
        return None


def _local_stamp():
    try:
        with open(STAMP_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _use(path):
    """db.get_conn()이 로컬 SQLite 스냅샷을 읽도록 config 고정."""
    config.DB_PATH = path
    config.DATABASE_URL = ""
    config.TURSO_DATABASE_URL = ""


def _read_snapshot_at(path):
    import sqlite3
    try:
        con = sqlite3.connect(path)
        row = con.execute("SELECT value FROM meta WHERE key='snapshot_at'").fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def ensure():
    """스냅샷을 준비하고 (로컬 경로, 생성시각)을 반환. 실패 시 SnapshotUnavailable."""
    # 로컬 개발용: 명시 경로 우선
    local = os.environ.get("SNAPSHOT_DB")
    if local:
        if not os.path.exists(local):
            raise SnapshotUnavailable(f"SNAPSHOT_DB not found: {local}")
        _use(local)
        return local, _read_snapshot_at(local)

    os.makedirs(CACHE_DIR, exist_ok=True)
    rstamp = _remote_stamp()
    need = (not os.path.exists(DB_FILE)) or (rstamp and rstamp != _local_stamp())

    if need:
        try:
            blob = _get(GZ_URL, timeout=180)
        except Exception as e:
            if os.path.exists(DB_FILE):        # 새로고침 실패 → 있던 스냅샷으로 버팀
                _use(DB_FILE)
                return DB_FILE, _read_snapshot_at(DB_FILE)
            raise SnapshotUnavailable(f"스냅샷 다운로드 실패: {e}") from e
        tmp_gz = DB_FILE + ".gz"
        with open(tmp_gz, "wb") as f:
            f.write(blob)
        tmp_db = DB_FILE + ".tmp"
        with gzip.open(tmp_gz, "rb") as f_in, open(tmp_db, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.replace(tmp_db, DB_FILE)
        os.remove(tmp_gz)
        if rstamp:
            with open(STAMP_FILE, "w", encoding="utf-8") as f:
                f.write(rstamp)

    _use(DB_FILE)
    return DB_FILE, _read_snapshot_at(DB_FILE)

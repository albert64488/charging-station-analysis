"""정적 스냅샷 빌더 — 라이브 DB 의존을 없애기 위한 압축 SQLite 산출물 생성.

앱이 매 요청마다 라이브 DB(Neon)를 때리는 대신, 수집기가 주기적으로 이 스냅샷을
만들어 발행하고 앱은 그 파일만 읽는다. → DB 다운/콜드스타트/용량 문제로부터 앱을 분리.

스냅샷 내용 (snapshot.db):
- station_stats : (충전소 × 급속/완속) 단위의 '가산 가능한 부분합'. 전국/지역/CPO/
                  타입필터/가중·단순 집계를 510k행 pandas 로드 없이 합산만으로 재구성.
- stations/chargers/current_state/charger_stats : 충전소 상세·실시간·검색·반경분석용 원본.
- meta : observation_start_at / last_poll_at / last_refresh_at + snapshot_at(생성시각).

부분합 컬럼(합산으로 임의 그룹 재집계 → 정확):
  cnt, sum_output,
  util_w_sum = Σ(이용률×output)  → 가중 이용률 = Σutil_w_sum / Σsum_output
  util_sum   = Σ(이용률)          → 단순 이용률 = Σutil_sum / Σcnt
  fault_sum  = Σ(장애율)          → 장애율 = Σfault_sum / Σcnt
  charge_h_sum, obs_h_sum        → 평균 충전/관측시간 = Σ / Σcnt

실행:
  python scripts/build_snapshot.py                  # 기본 out=data/snapshot.db(+.gz)
  python scripts/build_snapshot.py --out path.db --no-gzip
"""
import argparse
import gzip
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from src import calculator, db, util  # noqa: E402

RAW_TABLES = ["stations", "chargers", "current_state", "charger_stats"]

SNAP_SCHEMA = """
CREATE TABLE station_stats (
    stat_id      TEXT,
    is_fast      INTEGER,          -- 1 급속 / 0 완속
    busi_nm      TEXT,             -- 정규화된 운영사명 (충전소 내 혼재 대비 그룹키)
    stat_nm      TEXT,
    addr         TEXT,
    lat          REAL,
    lng          REAL,
    zcode        TEXT,
    kind         TEXT,             -- 충전소 구분 대분류
    kind_detail  TEXT,             -- 충전소 구분 상세
    cnt          INTEGER,          -- 이 (충전소,타입,운영사)의 충전기 수
    sum_output   REAL,             -- Σ output(kW)
    util_w_sum   REAL,             -- Σ(이용률 × output)
    util_sum     REAL,             -- Σ(이용률)
    fault_sum    REAL,             -- Σ(장애율)
    charge_h_sum REAL,             -- Σ(충전시간h)
    obs_h_sum    REAL,             -- Σ(관측시간h)
    PRIMARY KEY (stat_id, is_fast, busi_nm)
);
CREATE INDEX idx_ss_zcode ON station_stats(zcode);
CREATE INDEX idx_ss_busi  ON station_stats(busi_nm);
"""


def build_partials():
    """load_durations(전국) → (충전소×급속완속) 부분합 DataFrame."""
    ch = calculator.load_durations()  # 충전기별, busi_nm 정규화·이용률 계산 완료
    if ch.empty:
        return pd.DataFrame()

    # addr/kind는 load_durations에 없음 → stations에서 병합
    with db.get_conn() as conn:
        smeta = db.fetch_df(conn, "SELECT stat_id, addr, kind, kind_detail FROM stations")
    ch = ch.merge(smeta, on="stat_id", how="left")

    ch["out"] = ch["output"].fillna(0.0)
    ch["util_w"] = ch["이용률"] * ch["out"]
    # (충전소, 급속/완속, 운영사) 단위 부분합 — 한 충전소에 운영사가 섞여도 정확히 보존
    g = ch.groupby(["stat_id", "is_fast", "busi_nm"], sort=False, dropna=False)
    part = g.agg(
        stat_nm=("stat_nm", "first"),
        addr=("addr", "first"),
        lat=("lat", "first"),
        lng=("lng", "first"),
        zcode=("zcode", "first"),
        kind=("kind", "first"),
        kind_detail=("kind_detail", "first"),
        cnt=("charger_key", "size"),
        sum_output=("out", "sum"),
        util_w_sum=("util_w", "sum"),
        util_sum=("이용률", "sum"),
        fault_sum=("장애율", "sum"),
        charge_h_sum=("충전시간(h)", "sum"),
        obs_h_sum=("관측시간(h)", "sum"),
    ).reset_index()
    part["is_fast"] = part["is_fast"].fillna(0).astype(int)
    return part


def copy_raw_tables(dst):
    """원본 테이블(상세/검색/실시간용)을 스냅샷 sqlite로 복사."""
    for t in RAW_TABLES:
        with db.get_conn() as conn:
            df = db.fetch_df(conn, f"SELECT * FROM {t}")
        df.to_sql(t, dst, if_exists="replace", index=False)
        print(f"  복사 {t}: {len(df):,}행")
    # 드릴다운 성능용 인덱스
    dst.execute("CREATE INDEX IF NOT EXISTS idx_ch_stat ON chargers(stat_id)")
    dst.execute("CREATE INDEX IF NOT EXISTS idx_ch_key ON chargers(charger_key)")
    dst.execute("CREATE INDEX IF NOT EXISTS idx_cs_key ON current_state(charger_key)")
    dst.execute("CREATE INDEX IF NOT EXISTS idx_st_id ON stations(stat_id)")


def copy_meta(dst):
    """meta 복사 + snapshot_at 기록."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    dst.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    dst.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    [(k, v) for k, v in rows])
    dst.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('snapshot_at', ?)",
                (util.now_str(),))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("data", "snapshot.db"))
    ap.add_argument("--no-gzip", action="store_true")
    args = ap.parse_args()

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if os.path.exists(out):
        os.remove(out)

    print("1) 부분합 station_stats 계산…")
    part = build_partials()
    if part.empty:
        print("소스 데이터가 없습니다. 중단.")
        sys.exit(1)
    print(f"   station_stats: {len(part):,}행 "
          f"(충전소 {part['stat_id'].nunique():,}곳)")

    print("2) 스냅샷 SQLite 작성…")
    dst = sqlite3.connect(out)
    try:
        for stmt in SNAP_SCHEMA.split(";"):
            if stmt.strip():
                dst.execute(stmt)
        part.to_sql("station_stats", dst, if_exists="append", index=False)
        print("3) 원본 테이블 복사(상세/검색/실시간용)…")
        copy_raw_tables(dst)
        copy_meta(dst)
        dst.commit()
        dst.execute("VACUUM")
        dst.commit()
    finally:
        dst.close()

    size_mb = os.path.getsize(out) / 1e6
    print(f"   완료: {out} ({size_mb:.1f} MB)")

    if not args.no_gzip:
        gz = out + ".gz"
        with open(out, "rb") as f_in, gzip.open(gz, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_mb = os.path.getsize(gz) / 1e6
        print(f"   압축: {gz} ({gz_mb:.1f} MB, {gz_mb / size_mb * 100:.0f}%)")


if __name__ == "__main__":
    main()

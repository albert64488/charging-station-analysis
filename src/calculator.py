"""시간(지속구간) 기반 이용률/가동률 산정.

정의 (이벤트 기반):
- 이용률 = 충전중 시간 ÷ 전체 관측시간 × 100
- 가동률 = (사용가능 + 충전중) 시간 ÷ 전체 관측시간 × 100
- 장애율 = (통신이상 + 운영중지 + 점검중 + 미확인) 시간 ÷ 전체 관측시간 × 100

각 충전기의 상태구간(state_intervals)과 현재 열린 상태(current_state)를
분석 구간 [start, end]로 잘라(clip) 지속시간을 합산한다.
충전소 이용률은 출력(kW) 가중평균(권장) 또는 단순평균.
"""
import datetime

import numpy as np
import pandas as pd

import config
from src import db, util


def _meta_df(conn, zcode=None):
    sql = """
        SELECT c.charger_key, c.stat_id, c.chger_id, c.is_fast, c.output, c.busi_nm,
               s.stat_nm, s.lat, s.lng, s.zcode
        FROM chargers c JOIN stations s ON c.stat_id = s.stat_id
    """
    params = []
    if zcode:
        sql += " WHERE s.zcode = ?"
        params.append(str(zcode))
    return db.fetch_df(conn, sql, params)


def _earliest(conn):
    """관측 시작 기준점 = 닫힌 구간/현재상태 진입시각 중 가장 이른 값."""
    vals = []
    for q in ("SELECT MIN(start_dt) FROM state_intervals",
              "SELECT MIN(since_dt) FROM current_state"):
        v = conn.execute(q).fetchone()[0]
        if v:
            vals.append(v)
    return min(vals) if vals else None


def _intervals_df(conn, zcode, t0, t1):
    """[t0,t1]과 겹치는 닫힌 구간 + 현재 열린 구간 (지역 필터는 SQL에서)."""
    zc = " AND s.zcode = ?" if zcode else ""
    closed = db.fetch_df(
        conn,
        "SELECT i.charger_key, i.stat, i.start_dt, i.end_dt "
        "FROM state_intervals i "
        "JOIN chargers c ON i.charger_key = c.charger_key "
        "JOIN stations s ON c.stat_id = s.stat_id "
        "WHERE i.end_dt >= ? AND i.start_dt <= ?" + zc,
        [t0, t1] + ([str(zcode)] if zcode else []),
    )
    open_ = db.fetch_df(
        conn,
        "SELECT cs.charger_key, cs.stat, cs.since_dt AS start_dt "
        "FROM current_state cs "
        "JOIN chargers c ON cs.charger_key = c.charger_key "
        "JOIN stations s ON c.stat_id = s.stat_id "
        "WHERE cs.since_dt <= ?" + zc,
        [t1] + ([str(zcode)] if zcode else []),
    )
    open_["end_dt"] = t1  # 열린 구간 끝 = 분석구간 끝
    return pd.concat(
        [closed, open_[["charger_key", "stat", "start_dt", "end_dt"]]],
        ignore_index=True,
    )


def load_durations(zcode=None, start=None, end=None):
    """충전기별 상태 카테고리 지속시간(초) 집계 DataFrame."""
    t1 = end or util.now_str()
    with db.get_conn() as conn:
        meta = _meta_df(conn, zcode)
        if meta.empty:
            return pd.DataFrame()
        if start is None:
            start = _earliest(conn) or t1
        iv = _intervals_df(conn, zcode, start, t1)

    if iv.empty:
        return pd.DataFrame()

    t0 = pd.to_datetime(start)
    t1d = pd.to_datetime(t1)
    s = pd.to_datetime(iv["start_dt"]).clip(lower=t0)
    e = pd.to_datetime(iv["end_dt"]).clip(upper=t1d)
    iv["sec"] = (e - s).dt.total_seconds().clip(lower=0)
    iv["charging"] = iv["stat"].isin(config.CHARGING_STATES) * iv["sec"]
    iv["fault"] = iv["stat"].isin(config.FAULT_STATES) * iv["sec"]

    g = iv.groupby("charger_key").agg(
        total_sec=("sec", "sum"),
        charging_sec=("charging", "sum"),
        fault_sec=("fault", "sum"),
    ).reset_index()
    g = g[g["total_sec"] > 0]

    df = g.merge(meta, on="charger_key", how="left")
    df["이용률"] = (df["charging_sec"] / df["total_sec"] * 100).round(2)
    df["장애율"] = (df["fault_sec"] / df["total_sec"] * 100).round(2)
    df["관측시간(h)"] = (df["total_sec"] / 3600).round(1)
    df["충전기구분"] = df["is_fast"].map({1: "급속", 0: "완속"})
    return df


def _agg_rates(charger_df, key, method):
    """key(충전소/CPO) 단위로 이용률·가동률·장애율 등을 벡터화 집계."""
    d = charger_df.copy()
    d["w"] = d["output"].fillna(0)
    d["util_w"] = d["이용률"] * d["w"]
    g = d.groupby(key, sort=False).agg(
        sum_w=("w", "sum"),
        util_w=("util_w", "sum"),
        util_m=("이용률", "mean"),
        장애율=("장애율", "mean"),
        충전기수=("charger_key", "size"),
        급속=("is_fast", "sum"),
        관측시간=("관측시간(h)", "sum"),
    )
    if method == "weighted":
        wmask = g["sum_w"] > 0
        denom = g["sum_w"].where(wmask, 1)
        g["이용률"] = np.where(wmask, g["util_w"] / denom, g["util_m"])
    else:
        g["이용률"] = g["util_m"]
    g["급속"] = g["급속"].astype(int)
    g["완속"] = (g["충전기수"] - g["급속"]).astype(int)
    g["이용률"] = g["이용률"].round(2)
    g["장애율"] = g["장애율"].round(2)
    g["관측시간(h)"] = g["관측시간"].round(1)
    return g


def station_summary(charger_df, method="weighted"):
    """충전소 단위 집계 (출력 가중 / 단순)."""
    if charger_df is None or charger_df.empty:
        return pd.DataFrame()
    g = _agg_rates(charger_df, "stat_id", method)
    names = charger_df.groupby("stat_id", sort=False).agg(
        충전소명=("stat_nm", "first"), 운영사=("busi_nm", "first"))
    g = g.join(names).reset_index()
    out = g[["stat_id", "충전소명", "운영사", "이용률", "장애율",
             "충전기수", "급속", "완속", "관측시간(h)"]]
    return out.sort_values("이용률", ascending=False).reset_index(drop=True)


def cpo_summary(charger_df, method="weighted"):
    """운영사(CPO) 단위 집계 — 충전사업자별 운영현황 비교."""
    if charger_df is None or charger_df.empty:
        return pd.DataFrame()
    g = _agg_rates(charger_df, "busi_nm", method)
    nst = charger_df.groupby("busi_nm", sort=False).agg(충전소수=("stat_id", "nunique"))
    g = g.join(nst).reset_index().rename(columns={"busi_nm": "운영사(CPO)"})
    g["운영사(CPO)"] = g["운영사(CPO)"].replace("", "(미상)").fillna("(미상)")
    out = g[["운영사(CPO)", "충전소수", "충전기수", "급속", "완속",
             "이용률", "장애율", "관측시간(h)"]]
    return out.sort_values("충전기수", ascending=False).reset_index(drop=True)


def live_status(stat_id):
    """충전소 1곳의 실시간 충전기 상태 (무공해차 앱 상세화면 형태).

    반환: (충전소정보 dict, 표시용 DataFrame)
    """
    with db.get_conn() as conn:
        sinfo = db.fetch_df(
            conn, "SELECT stat_nm, addr, busi_nm FROM stations WHERE stat_id = ?", [stat_id])
        df = db.fetch_df(
            conn,
            "SELECT c.chger_id, c.chger_type, c.is_fast, c.output, "
            "cs.stat, cs.since_dt, cs.stat_upd_dt "
            "FROM chargers c JOIN current_state cs ON c.charger_key = cs.charger_key "
            "WHERE c.stat_id = ? ORDER BY c.chger_id",
            [stat_id],
        )
    info = sinfo.iloc[0].to_dict() if not sinfo.empty else {"stat_nm": "", "addr": "", "busi_nm": ""}
    if df.empty:
        return info, df

    now = pd.Timestamp(util.now_dt())  # KST 기준

    def status_text(row):
        s = int(row["stat"])
        if s == 3:  # 충전중 → 경과시간
            since = pd.to_datetime(row["since_dt"], errors="coerce")
            if pd.notna(since):
                mins = max(0, int((now - since).total_seconds() // 60))
                h, m = divmod(mins, 60)
                return f"{h}시간{m}분 충전중"
            return "충전중"
        return config.STATUS_NAMES.get(s, str(s))

    def gubun(row):
        kw = int(row["output"]) if pd.notna(row["output"]) else "-"
        return f"{'급속' if row['is_fast'] == 1 else '완속'} ({kw}kW)"

    out = pd.DataFrame({
        "충전기ID": df["chger_id"],
        "구분": df.apply(gubun, axis=1),
        "충전기타입": df["chger_type"].map(config.CHGER_TYPE_NAMES).fillna(df["chger_type"]),
        "현재상태": df.apply(status_text, axis=1),
        "갱신일시": df["stat_upd_dt"],
    })
    return info, out


def summary(zcode=None, start=None, end=None, method="weighted"):
    chargers = load_durations(zcode=zcode, start=start, end=end)
    stations = station_summary(chargers, method=method)
    return stations, chargers

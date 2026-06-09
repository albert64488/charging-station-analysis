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


def _intervals_df(conn, keys, t0, t1):
    """[t0,t1]과 겹치는 닫힌 구간 + 현재 열린 구간."""
    closed = db.fetch_df(
        conn,
        "SELECT charger_key, stat, start_dt, end_dt FROM state_intervals "
        "WHERE end_dt >= ? AND start_dt <= ?",
        [t0, t1],
    )
    open_ = db.fetch_df(
        conn,
        "SELECT charger_key, stat, since_dt AS start_dt FROM current_state "
        "WHERE since_dt <= ?",
        [t1],
    )
    open_["end_dt"] = t1  # 열린 구간 끝 = 분석구간 끝
    df = pd.concat([closed, open_[["charger_key", "stat", "start_dt", "end_dt"]]],
                   ignore_index=True)
    if not df.empty:
        df = df[df["charger_key"].isin(keys)]
    return df


def load_durations(zcode=None, start=None, end=None):
    """충전기별 상태 카테고리 지속시간(초) 집계 DataFrame."""
    t1 = end or util.now_str()
    with db.get_conn() as conn:
        meta = _meta_df(conn, zcode)
        if meta.empty:
            return pd.DataFrame()
        keys = set(meta["charger_key"])
        if start is None:
            row = conn.execute("SELECT MIN(start_dt) FROM state_intervals").fetchone()
            start = row[0] or t1
        iv = _intervals_df(conn, keys, start, t1)

    if iv.empty:
        return pd.DataFrame()

    t0 = pd.to_datetime(start)
    t1d = pd.to_datetime(t1)
    s = pd.to_datetime(iv["start_dt"]).clip(lower=t0)
    e = pd.to_datetime(iv["end_dt"]).clip(upper=t1d)
    iv["sec"] = (e - s).dt.total_seconds().clip(lower=0)
    iv["charging"] = iv["stat"].isin(config.CHARGING_STATES) * iv["sec"]
    iv["operational"] = iv["stat"].isin(config.OPERATIONAL_STATES) * iv["sec"]
    iv["fault"] = iv["stat"].isin(config.FAULT_STATES) * iv["sec"]

    g = iv.groupby("charger_key").agg(
        total_sec=("sec", "sum"),
        charging_sec=("charging", "sum"),
        operational_sec=("operational", "sum"),
        fault_sec=("fault", "sum"),
    ).reset_index()
    g = g[g["total_sec"] > 0]

    df = g.merge(meta, on="charger_key", how="left")
    df["이용률"] = (df["charging_sec"] / df["total_sec"] * 100).round(2)
    df["가동률"] = (df["operational_sec"] / df["total_sec"] * 100).round(2)
    df["장애율"] = (df["fault_sec"] / df["total_sec"] * 100).round(2)
    df["관측시간(h)"] = (df["total_sec"] / 3600).round(1)
    df["충전기구분"] = df["is_fast"].map({1: "급속", 0: "완속"})
    return df


def _agg_rates(charger_df, key, method):
    """key(충전소/CPO) 단위로 이용률·가동률·장애율 등을 벡터화 집계."""
    d = charger_df.copy()
    d["w"] = d["output"].fillna(0)
    d["util_w"] = d["이용률"] * d["w"]
    d["avail_w"] = d["가동률"] * d["w"]
    g = d.groupby(key, sort=False).agg(
        sum_w=("w", "sum"),
        util_w=("util_w", "sum"),
        avail_w=("avail_w", "sum"),
        util_m=("이용률", "mean"),
        avail_m=("가동률", "mean"),
        장애율=("장애율", "mean"),
        충전기수=("charger_key", "size"),
        급속=("is_fast", "sum"),
        관측시간=("관측시간(h)", "sum"),
    )
    if method == "weighted":
        wmask = g["sum_w"] > 0
        denom = g["sum_w"].where(wmask, 1)
        g["이용률"] = np.where(wmask, g["util_w"] / denom, g["util_m"])
        g["가동률"] = np.where(wmask, g["avail_w"] / denom, g["avail_m"])
    else:
        g["이용률"] = g["util_m"]
        g["가동률"] = g["avail_m"]
    g["급속"] = g["급속"].astype(int)
    g["완속"] = (g["충전기수"] - g["급속"]).astype(int)
    g["이용률"] = g["이용률"].round(2)
    g["가동률"] = g["가동률"].round(2)
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
    out = g[["stat_id", "충전소명", "운영사", "이용률", "가동률", "장애율",
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
             "이용률", "가동률", "장애율", "관측시간(h)"]]
    return out.sort_values("충전기수", ascending=False).reset_index(drop=True)


def summary(zcode=None, start=None, end=None, method="weighted"):
    chargers = load_durations(zcode=zcode, start=start, end=end)
    stations = station_summary(chargers, method=method)
    return stations, chargers

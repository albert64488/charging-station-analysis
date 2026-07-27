"""시간 기반 이용률/장애율 산정 (집계 카운터 방식).

정의:
- 이용률 = 충전중 시간 ÷ 전체 관측시간 × 100
- 장애율 = (통신이상 + 운영중지 + 점검중 + 미확인) 시간 ÷ 전체 관측시간 × 100

충전중/장애 시간은 충전기별 누적 카운터(charger_stats)에 현재 열린 상태
(current_state)의 진행분을 더해 구한다. 관측창 = 수집시작(observation_start_at)~지금.
충전소 이용률은 출력(kW) 가중평균(권장) 또는 단순평균.
"""
import datetime
import math

import numpy as np
import pandas as pd

import config
from src import db, util


def _earliest(conn):
    """관측 시작 기준점 = 수집 시작 시각(observation_start_at)."""
    obs = db.get_meta(conn, "observation_start_at")
    if obs:
        return obs
    return conn.execute("SELECT MIN(since_dt) FROM current_state").fetchone()[0]


def load_durations(zcode=None, start=None, end=None, stat_id=None, stat_ids=None):
    """충전기별 이용률·장애율·충전시간 (누적 카운터 + 현재 진행분).

    이용률 = (누적 충전시간 + 현재 충전 진행분) / 관측창(수집시작~지금).
    """
    t1 = end or util.now_str()
    sql = (
        "SELECT c.charger_key, c.stat_id, c.chger_id, c.is_fast, c.output, c.busi_nm, "
        "s.stat_nm, s.lat, s.lng, s.zcode, cs.stat, cs.since_dt, "
        "COALESCE(st.charging_sec,0) AS charging_sec, COALESCE(st.fault_sec,0) AS fault_sec "
        "FROM chargers c "
        "JOIN stations s ON c.stat_id = s.stat_id "
        "JOIN current_state cs ON c.charger_key = cs.charger_key "
        "LEFT JOIN charger_stats st ON c.charger_key = st.charger_key "
        "WHERE 1=1"
    )
    params = []
    if zcode:
        sql += " AND s.zcode = ?"
        params.append(str(zcode))
    if stat_id:
        sql += " AND s.stat_id = ?"
        params.append(stat_id)
    if stat_ids:
        sql += " AND s.stat_id IN (%s)" % ",".join("?" * len(stat_ids))
        params.extend(stat_ids)

    with db.get_conn() as conn:
        obs_start = _earliest(conn)
        df = db.fetch_df(conn, sql, params)
    if df.empty:
        return df

    # 운영사명 정규화: (주)/㈜/브랜드 변형을 대표명으로 통합 (집계 정확도)
    df["busi_nm"] = df["busi_nm"].map(config.normalize_cpo)

    t1d = pd.to_datetime(t1)
    obs = pd.to_datetime(obs_start) if obs_start else t1d
    window = max((t1d - obs).total_seconds(), 1.0)
    since = pd.to_datetime(df["since_dt"]).clip(lower=obs)
    open_dur = (t1d - since).dt.total_seconds().clip(lower=0)
    charging = df["charging_sec"].fillna(0) + open_dur.where(df["stat"] == 3, 0.0)
    fault = df["fault_sec"].fillna(0) + open_dur.where(df["stat"].isin(config.FAULT_STATES), 0.0)

    df["이용률"] = (charging / window * 100).clip(0, 100).round(2)
    df["장애율"] = (fault / window * 100).clip(0, 100).round(2)
    df["충전시간(h)"] = (charging / 3600).round(1)
    df["관측시간(h)"] = round(window / 3600, 1)
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
        충전시간=("충전시간(h)", "mean"),   # 충전기 1대당 평균 충전시간
        관측시간=("관측시간(h)", "mean"),   # 충전기 1대당 평균 관측시간
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
    g["충전시간(h)"] = g["충전시간"].round(1)
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
             "충전기수", "급속", "완속", "충전시간(h)", "관측시간(h)"]]
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
             "이용률", "장애율", "충전시간(h)", "관측시간(h)"]]
    return out.sort_values("충전기수", ascending=False).reset_index(drop=True)


def search_stations(term, limit=100):
    """충전소명 또는 주소로 전국 검색 (매칭만 조회 → 가벼움)."""
    like = f"%{term}%"
    with db.get_conn() as conn:
        df = db.fetch_df(
            conn,
            "SELECT stat_id, stat_nm, busi_nm, addr, zcode FROM stations "
            "WHERE stat_nm LIKE ? OR addr LIKE ? ORDER BY stat_nm LIMIT ?",
            [like, like, limit],
        )
    if not df.empty:
        df["busi_nm"] = df["busi_nm"].map(config.normalize_cpo)
    return df.rename(columns={"stat_nm": "충전소명", "busi_nm": "운영사", "addr": "주소"})


def nearby_stations(stat_id, radius_km=3.0):
    """선택 충전소 반경 radius_km 내 충전소 + 거리 + 이용률. (중심좌표, DataFrame) 반환."""
    with db.get_conn() as conn:
        c = db.fetch_df(conn, "SELECT lat, lng FROM stations WHERE stat_id = ?", [stat_id])
        if c.empty or pd.isna(c["lat"].iloc[0]) or pd.isna(c["lng"].iloc[0]):
            return None, pd.DataFrame()
        clat, clng = float(c["lat"].iloc[0]), float(c["lng"].iloc[0])
        dlat = radius_km / 111.0
        dlng = radius_km / (111.0 * max(math.cos(math.radians(clat)), 0.01))
        cand = db.fetch_df(
            conn,
            "SELECT stat_id, stat_nm, busi_nm, addr, lat, lng FROM stations "
            "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
            [clat - dlat, clat + dlat, clng - dlng, clng + dlng],
        )
    if cand.empty:
        return (clat, clng), pd.DataFrame()

    lat2 = np.radians(cand["lat"].astype(float))
    lng2 = np.radians(cand["lng"].astype(float))
    lat1, lng1 = math.radians(clat), math.radians(clng)
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2)
    cand["거리(km)"] = (6371.0 * 2 * np.arcsin(np.sqrt(a))).round(2)
    cand = cand[cand["거리(km)"] <= radius_km].copy()
    if cand.empty:
        return (clat, clng), pd.DataFrame()

    ch = load_durations(stat_ids=cand["stat_id"].tolist())
    cols = ["stat_id", "이용률", "장애율", "충전기수", "급속", "완속"]
    summ = station_summary(ch)[cols] if not ch.empty else pd.DataFrame(columns=cols)
    out = cand.merge(summ, on="stat_id", how="left").rename(
        columns={"stat_nm": "충전소명", "busi_nm": "운영사", "addr": "주소",
                 "lat": "latitude", "lng": "longitude"})
    return (clat, clng), out.sort_values("거리(km)").reset_index(drop=True)


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
    info["busi_nm"] = config.normalize_cpo(info.get("busi_nm"))
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

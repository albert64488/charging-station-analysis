"""수집 로직 (집계 기반).

- refresh_full() : getChargerInfo 전수 → 메타 신규 + 현재상태 보정 + 누적 카운터 갱신
- poll_changes() : getChargerStatus 델타 → 변경분만 카운터 갱신
- seed_sample()  : API 키 없이 검증용 샘플

원시 구간(state_intervals)을 저장하지 않고, 상태가 끝날 때마다 그 지속시간을
충전기별 누적 카운터(charger_stats: charging_sec/fault_sec)에 더한다(관측시작 기준 클립).
→ 저장량이 충전기 수만큼 고정(안 늘어남). 이용률 = (누적 + 현재진행분) / 관측창.
"""
import datetime

import config
from src import api_client, db, sample_data, util


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_stat(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 9


def _dur_sec(start_iso, end_iso, floor_iso=None):
    """[start,end] 지속시간(초). floor 이전은 제외(관측창 클립). ISO 문자열은 사전식=시간순."""
    s = max(start_iso, floor_iso) if floor_iso else start_iso
    if not end_iso or end_iso <= s:
        return 0.0
    try:
        d = (datetime.datetime.strptime(end_iso, util.FMT)
             - datetime.datetime.strptime(s, util.FMT)).total_seconds()
        return max(0.0, d)
    except ValueError:
        return 0.0


def _meta_rows(item, updated_at):
    """item → (station_row, charger_row, charger_key)."""
    stat_id = item.get("statId")
    chger_id = item.get("chgerId")
    if not stat_id or not chger_id:
        return None
    charger_key = f"{stat_id}-{chger_id}"
    busi_nm = item.get("busiNm") or item.get("bnm") or ""
    chger_type = (item.get("chgerType") or "").zfill(2)
    output = _to_float(item.get("output"))
    station_row = (
        stat_id, item.get("statNm", ""), item.get("addr", ""),
        _to_float(item.get("lat")), _to_float(item.get("lng")),
        item.get("busiId", ""), busi_nm, item.get("zcode", ""), updated_at,
    )
    charger_row = (
        charger_key, stat_id, chger_id, chger_type,
        config.classify_fast(chger_type, output), output, busi_nm, updated_at,
    )
    return station_row, charger_row, charger_key


def _apply_change(current, stats, key, new_stat, change_dt, obs_start, state_rows, updated_at):
    """변경 시 직전 상태의 지속시간을 누적 카운터에 더하고 현재상태 갱신.

    current: {key: (stat, since_dt)} · stats: {key: [charging_sec, fault_sec]} (in-memory 갱신)
    """
    prev = current.get(key)
    if prev is None:
        current[key] = (new_stat, change_dt)
        stats.setdefault(key, [0.0, 0.0])
        state_rows.append((key, new_stat, change_dt, change_dt, updated_at))
        return
    prev_stat, prev_since = prev
    # 같은 상태 + 진입시각이 더 최신도 아님 → 진짜 변화 없음
    if prev_stat == new_stat and change_dt <= prev_since:
        return
    end_dt = change_dt if change_dt >= prev_since else prev_since
    dur = _dur_sec(prev_since, end_dt, obs_start)
    st = stats.setdefault(key, [0.0, 0.0])
    if prev_stat in config.CHARGING_STATES:
        st[0] += dur
    elif prev_stat in config.FAULT_STATES:
        st[1] += dur
    current[key] = (new_stat, change_dt)
    state_rows.append((key, new_stat, change_dt, change_dt, updated_at))


def _get_obs_start(conn, updated_at):
    obs = db.get_meta(conn, "observation_start_at")
    if not obs:
        obs = updated_at
        db.set_meta(conn, "observation_start_at", obs)
    return obs


# 전국 시·도 지역코드 (전수 refresh를 지역별로 분할 → API 504에 견고)
ZCODES_ALL = ["11", "26", "27", "28", "29", "30", "31", "36", "41",
              "43", "44", "46", "47", "48", "50", "51", "52"]


def _refresh_region(zcode, zscode, updated_at, obs_start):
    """한 지역 전수 조회 → 메타 신규 + 현재상태/누적카운터 갱신 (지역별 커밋)."""
    items = api_client.fetch_charger_info(zcode=zcode, zscode=zscode)

    station_rows, charger_rows, changes = {}, [], []
    for item in items:
        m = _meta_rows(item, updated_at)
        if not m:
            continue
        srow, crow, key = m
        station_rows[srow[0]] = srow
        charger_rows.append(crow)
        changes.append((key, _to_stat(item.get("stat")),
                        util.parse_stat_dt(item.get("statUpdDt"), default=updated_at)))

    keys = [c[0] for c in charger_rows]
    sids = list(station_rows.keys())
    with db.get_conn() as conn:
        known_ch = db.known_charger_keys(conn, keys)
        known_st = db.known_station_ids(conn, sids)
        db.upsert_stations(conn, [r for sid, r in station_rows.items() if sid not in known_st])
        new_chargers = [r for r in charger_rows if r[0] not in known_ch]
        db.upsert_chargers(conn, new_chargers)

        current = db.load_current_states(conn, keys)
        stats = db.load_stats(conn, keys)
        state_rows = []
        for key, stat, change_dt in changes:
            _apply_change(current, stats, key, stat, change_dt, obs_start, state_rows, updated_at)
        changed = [r[0] for r in state_rows]
        db.upsert_current_states(conn, state_rows)
        db.upsert_stats(conn, [(k, stats[k][0], stats[k][1]) for k in changed])
        conn.commit()
    return {"fetched": len(changes), "new_meta": len(new_chargers), "changed": len(state_rows)}


def refresh_full(zcode=None, zscode=None, use_sample=False):
    """전수 조회로 메타 갱신 + 현재상태/카운터 보정. 전국이면 시·도별로 분할."""
    if use_sample or not config.DATAGO_SERVICE_KEY:
        return seed_sample()
    updated_at = util.now_str()
    with db.get_conn() as conn:
        obs_start = _get_obs_start(conn, updated_at)
        conn.commit()

    if zcode or zscode:
        res = _refresh_region(zcode, zscode, updated_at, obs_start)
        with db.get_conn() as conn:
            db.set_meta(conn, "last_refresh_at", updated_at)
            conn.commit()
            res["stats"] = db.stats(conn)
        res.update({"source": "api", "op": "refresh", "failed": []})
        return res

    agg = {"fetched": 0, "new_meta": 0, "changed": 0}
    failed = []
    for z in ZCODES_ALL:
        try:
            r = _refresh_region(z, None, updated_at, obs_start)
            for k in agg:
                agg[k] += r[k]
        except Exception as e:
            failed.append(f"{z}({type(e).__name__})")
    with db.get_conn() as conn:
        db.set_meta(conn, "last_refresh_at", updated_at)
        conn.commit()
        s = db.stats(conn)
    return {"source": "api", "op": "refresh", "failed": failed, "stats": s, **agg}


def poll_changes(period=10, zcode=None, zscode=None):
    """델타 조회로 변경분만 카운터 갱신. 메타에 없는 충전기는 다음 refresh까지 보류."""
    if not config.DATAGO_SERVICE_KEY:
        raise RuntimeError("실데이터 폴링에는 API 키가 필요합니다.")
    items = api_client.fetch_charger_status(period=period, zcode=zcode, zscode=zscode)
    updated_at = util.now_str()
    poll_keys = [f"{it.get('statId')}-{it.get('chgerId')}"
                 for it in items if it.get("statId") and it.get("chgerId")]

    with db.get_conn() as conn:
        obs_start = _get_obs_start(conn, updated_at)
        current = db.load_current_states(conn, poll_keys)
        stats = db.load_stats(conn, poll_keys)
        state_rows = []
        skipped = 0
        for item in items:
            stat_id, chger_id = item.get("statId"), item.get("chgerId")
            if not stat_id or not chger_id:
                continue
            key = f"{stat_id}-{chger_id}"
            if key not in current:
                skipped += 1  # 아직 현재상태 없음 → 다음 refresh에서 편입
                continue
            stat = _to_stat(item.get("stat"))
            change_dt = util.parse_stat_dt(item.get("statUpdDt"), default=updated_at)
            _apply_change(current, stats, key, stat, change_dt, obs_start, state_rows, updated_at)
        changed = [r[0] for r in state_rows]
        db.upsert_current_states(conn, state_rows)
        db.upsert_stats(conn, [(k, stats[k][0], stats[k][1]) for k in changed])
        db.set_meta(conn, "last_poll_at", updated_at)
        conn.commit()
        s = db.stats(conn)
    return {"source": "api", "op": "poll", "period": period, "fetched": len(items),
            "changed": len(state_rows), "skipped_unknown": skipped, "stats": s}


def seed_sample(days=7):
    """샘플 메타 + 현재상태 + 누적 카운터 생성 (오프라인 검증용)."""
    updated_at = util.now_str()
    station_rows, charger_rows, state_rows, stat_rows = [], [], [], []
    for srow, crow, key, ivs, (cur_stat, cur_since) in sample_data.generate_event_history(days, updated_at):
        station_rows.append(srow)
        charger_rows.append(crow)
        state_rows.append((key, cur_stat, cur_since, cur_since, updated_at))
        ch = sum(_dur_sec(s, e) for (_k, st, s, e) in ivs if st in config.CHARGING_STATES)
        fa = sum(_dur_sec(s, e) for (_k, st, s, e) in ivs if st in config.FAULT_STATES)
        stat_rows.append((key, ch, fa))

    with db.get_conn() as conn:
        conn.execute("DELETE FROM current_state WHERE charger_key LIKE 'SMPL%'")
        conn.execute("DELETE FROM charger_stats WHERE charger_key LIKE 'SMPL%'")
        db.upsert_stations(conn, list({r[0]: r for r in station_rows}.values()))
        db.upsert_chargers(conn, charger_rows)
        db.upsert_current_states(conn, state_rows)
        db.upsert_stats(conn, stat_rows)
        db.set_meta(conn, "observation_start_at",
                    (util.now_dt() - datetime.timedelta(days=days)).strftime(util.FMT))
        conn.commit()
        s = db.stats(conn)
    return {"source": "sample", "op": "seed", "chargers": len(charger_rows), "stats": s}

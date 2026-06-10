"""수집 로직 (이벤트 기반).

- refresh_full() : getChargerInfo 전수 → 메타 upsert + 현재상태 시드/보정
- poll_changes() : getChargerStatus 델타 → 상태 변경분만 구간 기록
- seed_sample()  : API 키 없이 검증용 샘플 상태구간 생성

상태가 바뀌면 직전 상태의 닫힌 구간(state_intervals)을 쌓고,
새 상태를 current_state(열린 구간)로 갱신한다.
"""
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


def _apply_change(current, charger_key, new_stat, change_dt, intervals, state_rows, updated_at):
    """current_state와 비교해 변경 시 닫힌 구간 적립 + 새 상태 준비.

    current: {charger_key: (stat, since_dt)} (in-memory, 호출 중 갱신)
    """
    prev = current.get(charger_key)
    if prev is None:
        current[charger_key] = (new_stat, change_dt)
        state_rows.append((charger_key, new_stat, change_dt, change_dt, updated_at))
        return
    prev_stat, prev_since = prev
    if prev_stat == new_stat:
        return  # 상태 동일 → 구간 유지
    # 상태 변경: 직전 구간 닫기 (end는 start 이상으로 클램프)
    end_dt = change_dt if change_dt >= prev_since else prev_since
    intervals.append((charger_key, prev_stat, prev_since, end_dt))
    current[charger_key] = (new_stat, change_dt)
    state_rows.append((charger_key, new_stat, change_dt, change_dt, updated_at))


# 전국 시·도 지역코드 (전수 refresh를 지역별로 분할 → API 504에 견고)
ZCODES_ALL = ["11", "26", "27", "28", "29", "30", "31", "36", "41",
              "43", "44", "46", "47", "48", "50", "51", "52"]


def _refresh_region(zcode, zscode, updated_at):
    """한 지역 전수 조회 → 메타 신규분 + 현재상태 변경분 기록 (지역별 커밋)."""
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
        intervals, state_rows = [], []
        for key, stat, change_dt in changes:
            _apply_change(current, key, stat, change_dt, intervals, state_rows, updated_at)
        db.insert_intervals(conn, intervals)
        db.upsert_current_states(conn, state_rows)
        conn.commit()
    return {"fetched": len(changes), "new_meta": len(new_chargers),
            "new_intervals": len(intervals)}


def refresh_full(zcode=None, zscode=None, use_sample=False):
    """전수 조회로 메타 갱신 + 현재상태 보정. 전국이면 시·도별로 분할 수행."""
    if use_sample or not config.DATAGO_SERVICE_KEY:
        return seed_sample()
    updated_at = util.now_str()

    # 특정 지역 지정 시 단일 수행
    if zcode or zscode:
        res = _refresh_region(zcode, zscode, updated_at)
        with db.get_conn() as conn:
            res["stats"] = db.stats(conn)
        res.update({"source": "api", "op": "refresh", "failed": []})
        return res

    # 전국: 시·도별 순회 (한 지역 실패해도 나머지는 저장)
    agg = {"fetched": 0, "new_meta": 0, "new_intervals": 0}
    failed = []
    for z in ZCODES_ALL:
        try:
            r = _refresh_region(z, None, updated_at)
            for k in agg:
                agg[k] += r[k]
        except Exception as e:
            failed.append(f"{z}({type(e).__name__})")
    with db.get_conn() as conn:
        s = db.stats(conn)
    return {"source": "api", "op": "refresh", "failed": failed, "stats": s, **agg}


def poll_changes(period=10, zcode=None, zscode=None):
    """델타 조회로 변경분만 구간 기록. 메타에 없는 충전기는 다음 refresh까지 보류."""
    if not config.DATAGO_SERVICE_KEY:
        raise RuntimeError("실데이터 폴링에는 API 키가 필요합니다. 샘플은 seed_sample 사용.")
    items = api_client.fetch_charger_status(period=period, zcode=zcode, zscode=zscode)
    updated_at = util.now_str()

    # 변경분의 charger_key만 추려 그 범위만 DB 조회 (전체 51만 읽기 방지)
    poll_keys = [f"{it.get('statId')}-{it.get('chgerId')}"
                 for it in items if it.get("statId") and it.get("chgerId")]

    with db.get_conn() as conn:
        # current_state에 있으면 곧 메타도 있는 충전기 → 별도 known 조회 생략(왕복 절감)
        current = db.load_current_states(conn, poll_keys)
        intervals, state_rows = [], []
        skipped = 0
        for item in items:
            stat_id, chger_id = item.get("statId"), item.get("chgerId")
            if not stat_id or not chger_id:
                continue
            key = f"{stat_id}-{chger_id}"
            if key not in current:
                skipped += 1  # 아직 현재상태 없음 → 다음 refresh_full에서 편입
                continue
            stat = _to_stat(item.get("stat"))
            change_dt = util.parse_stat_dt(item.get("statUpdDt"), default=updated_at)
            _apply_change(current, key, stat, change_dt, intervals, state_rows, updated_at)
        db.insert_intervals(conn, intervals)
        db.upsert_current_states(conn, state_rows)
        conn.commit()
        s = db.stats(conn)
    return {"source": "api", "op": "poll", "period": period, "fetched": len(items),
            "changed": len(intervals), "skipped_unknown": skipped, "stats": s}


def seed_sample(days=7):
    """샘플 메타 + 상태구간 생성 (오프라인 검증용)."""
    updated_at = util.now_str()
    station_rows, charger_rows, intervals, state_rows = [], [], [], []
    for srow, crow, key, ivs, (cur_stat, cur_since) in sample_data.generate_event_history(days, updated_at):
        station_rows.append(srow)
        charger_rows.append(crow)
        intervals.extend(ivs)
        state_rows.append((key, cur_stat, cur_since, cur_since, updated_at))

    with db.get_conn() as conn:
        # 중복 방지: 샘플 재시드 시 기존 샘플 구간 제거
        conn.execute("DELETE FROM state_intervals WHERE charger_key LIKE 'SMPL%'")
        conn.execute("DELETE FROM current_state   WHERE charger_key LIKE 'SMPL%'")
        db.upsert_stations(conn, {r[0]: r for r in station_rows}.values())
        db.upsert_chargers(conn, charger_rows)
        db.insert_intervals(conn, intervals)
        db.upsert_current_states(conn, state_rows)
        conn.commit()
        s = db.stats(conn)
    return {"source": "sample", "op": "seed", "chargers": len(charger_rows),
            "new_intervals": len(intervals), "stats": s}

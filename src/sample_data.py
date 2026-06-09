"""API 키 없이 검증용 샘플 — 상태구간(이벤트) 타임라인 생성.

각 충전기에 대해 과거 days일간 사용가능↔충전중(가끔 점검) 전이를 시뮬레이션해
닫힌 구간 목록 + 현재 열린 상태를 만든다. 급속/완속에 따라 세션 길이가 다르다.
"""
import datetime
import random

import config
from src import util

_STATIONS = [
    {"statId": "SMPL0001", "statNm": "샘플 강남역 충전소", "addr": "서울 강남구 강남대로 396",
     "lat": 37.4979, "lng": 127.0276, "busiId": "BS01", "busiNm": "한국전력", "zcode": "11",
     "chargers": [("01", "04", 100), ("02", "04", 100), ("03", "02", 7)]},
    {"statId": "SMPL0002", "statNm": "샘플 잠실 충전소", "addr": "서울 송파구 올림픽로 300",
     "lat": 37.5133, "lng": 127.1028, "busiId": "BS02", "busiNm": "환경부", "zcode": "11",
     "chargers": [("01", "05", 200), ("02", "02", 7)]},
    {"statId": "SMPL0003", "statNm": "샘플 여의도 충전소", "addr": "서울 영등포구 여의대로 24",
     "lat": 37.5219, "lng": 126.9245, "busiId": "BS03", "busiNm": "차지비", "zcode": "11",
     "chargers": [("01", "04", 50), ("02", "04", 50), ("03", "04", 50)]},
    {"statId": "SMPL0004", "statNm": "샘플 홍대입구 충전소", "addr": "서울 마포구 양화로 160",
     "lat": 37.5572, "lng": 126.9245, "busiId": "BS02", "busiNm": "환경부", "zcode": "11",
     "chargers": [("01", "02", 7), ("02", "02", 7)]},
    {"statId": "SMPL0005", "statNm": "샘플 광화문 충전소", "addr": "서울 종로구 세종대로 175",
     "lat": 37.5720, "lng": 126.9769, "busiId": "BS04", "busiNm": "GS차지비", "zcode": "11",
     "chargers": [("01", "06", 150), ("02", "02", 7), ("03", "02", 7)]},
]


def _dur_min(stat, is_fast, rng):
    if stat == 3:      # 충전중
        return rng.randint(20, 60) if is_fast else rng.randint(120, 360)
    if stat in (4, 5): # 점검/운영중지
        return rng.randint(60, 600)
    return rng.randint(10, 120) if is_fast else rng.randint(30, 240)  # 사용가능(대기)


def _next_stat(cur, rng):
    if cur == 2:
        return 5 if rng.random() < 0.05 else 3   # 대기 → 충전(가끔 점검)
    return 2                                      # 충전/점검 → 대기


def _timeline(charger_key, is_fast, start, end, rng):
    intervals = []
    t = start
    cur = 2
    while True:
        dur = datetime.timedelta(minutes=_dur_min(cur, is_fast, rng))
        nxt = t + dur
        if nxt >= end:
            break  # 마지막 구간은 열린 상태로 둠
        intervals.append((charger_key, cur, t.strftime(util.FMT), nxt.strftime(util.FMT)))
        t, cur = nxt, _next_stat(cur, rng)
    return intervals, cur, t.strftime(util.FMT)


def generate_event_history(days, updated_at):
    """[(station_row, charger_row, charger_key, intervals, (cur_stat, cur_since)), ...]"""
    end = util.now_dt()
    start = end - datetime.timedelta(days=days)
    out = []
    for st in _STATIONS:
        srow = (st["statId"], st["statNm"], st["addr"], st["lat"], st["lng"],
                st["busiId"], st["busiNm"], st["zcode"], updated_at)
        for chger_id, chger_type, output in st["chargers"]:
            key = f"{st['statId']}-{chger_id}"
            is_fast = config.classify_fast(chger_type, output)
            crow = (key, st["statId"], chger_id, chger_type, is_fast, float(output),
                    st["busiNm"], updated_at)
            rng = random.Random(hash(key) & 0xFFFFFFFF)
            intervals, cur_stat, cur_since = _timeline(key, is_fast, start, end, rng)
            out.append((srow, crow, key, intervals, (cur_stat, cur_since)))
    return out

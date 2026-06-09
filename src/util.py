"""공통 유틸 — 시각 파싱/포맷.

data.go.kr의 모든 시각은 한국시간(KST). 서버가 UTC(클라우드)여도
일관되게 KST 기준으로 'now'를 계산한다.
"""
import datetime

FMT = "%Y-%m-%d %H:%M:%S"
KST = datetime.timezone(datetime.timedelta(hours=9))


def now_dt():
    """현재 한국시간(KST), tz 없는 naive datetime."""
    return datetime.datetime.now(KST).replace(tzinfo=None, microsecond=0)


def now_str():
    return now_dt().strftime(FMT)


def parse_stat_dt(raw, default=None):
    """API statUpdDt(yyyyMMddHHmmss 등)를 ISO 문자열로 변환.

    파싱 불가/공백이면 default(없으면 현재시각) 사용.
    """
    s = (raw or "").strip()
    if s.isdigit() and len(s) == 14:
        try:
            return datetime.datetime.strptime(s, "%Y%m%d%H%M%S").strftime(FMT)
        except ValueError:
            pass
    # 이미 ISO 형태면 그대로 시도
    try:
        return datetime.datetime.fromisoformat(s).strftime(FMT)
    except (ValueError, TypeError):
        return default if default is not None else now_str()

"""공통 유틸 — 시각 파싱/포맷."""
import datetime

FMT = "%Y-%m-%d %H:%M:%S"


def now_str():
    return datetime.datetime.now().strftime(FMT)


def now_dt():
    return datetime.datetime.now().replace(microsecond=0)


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

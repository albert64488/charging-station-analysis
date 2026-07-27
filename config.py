"""환경설정 및 도메인 상수 정의."""
import os
import re

from dotenv import load_dotenv

load_dotenv()

# --- 환경변수 ---
DATAGO_SERVICE_KEY = os.getenv("DATAGO_SERVICE_KEY", "").strip()
API_BASE = os.getenv("API_BASE", "http://apis.data.go.kr/B552584/EvCharger").strip()
ZCODE = os.getenv("ZCODE", "11").strip()
ZSCODE = os.getenv("ZSCODE", "").strip()          # 시군구 상세코드(선택). 비우면 시도 전체
NUM_OF_ROWS = int(os.getenv("NUM_OF_ROWS", "9999"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "90"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))          # 일시적 오류(504 등) 재시도 횟수
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "3"))    # 재시도 대기 기본초(점증)
DB_PATH = os.getenv("DB_PATH", "data/charging.db").strip()

# Postgres(Neon 등) — 설정 시 최우선 사용
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Turso(호스팅 SQLite) — DATABASE_URL 없을 때 사용
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
TURSO_REPLICA_PATH = os.getenv("TURSO_REPLICA_PATH", "data/replica.db").strip()

# --- 지역코드(zcode) → 시도명 ---
ZCODE_NAMES = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주",
    "30": "대전", "31": "울산", "36": "세종", "41": "경기", "43": "충북",
    "44": "충남", "46": "전남", "47": "경북", "48": "경남", "50": "제주",
    "51": "강원", "52": "전북", "42": "강원", "45": "전북",
}

# --- 상태코드(stat) 정의 ---
# 무공해차/한국환경공단 충전기 상태코드
STATUS_NAMES = {
    1: "통신이상",
    2: "사용가능",   # 충전대기 / 운영중
    3: "충전중",
    4: "운영중지",
    5: "점검중",
    9: "상태미확인",
}

# 가동률에 포함되는 정상 운영 상태 (사용가능 + 충전중)
OPERATIONAL_STATES = {2, 3}
# 이용률(충전중) 상태
CHARGING_STATES = {3}
# 장애/제외 상태 (고장/점검/통신이상/미확인)
FAULT_STATES = {1, 4, 5, 9}

# --- 충전기 타입(chgerType) ---
# 완속으로 분류할 타입 (AC완속, AC3상)
SLOW_TYPES = {"02", "07"}

# 충전기 타입 코드 → 표시명
CHGER_TYPE_NAMES = {
    "01": "DC차데모",
    "02": "AC완속",
    "03": "DC차데모+AC3상",
    "04": "DC콤보",
    "05": "DC차데모+DC콤보",
    "06": "DC차데모+AC3상+DC콤보",
    "07": "AC3상(완속)",
    "08": "DC콤보(완속)",
}


def classify_fast(chger_type, output) -> int:
    """급속(1)/완속(0) 분류. 타입 우선, 불명 시 출력(kW)으로 추정."""
    t = (chger_type or "").strip().zfill(2)
    if t in SLOW_TYPES:
        return 0
    if t and t != "00":
        return 1
    try:
        return 1 if float(output) >= 50 else 0
    except (TypeError, ValueError):
        return 0


def zcode_name(zcode: str) -> str:
    return ZCODE_NAMES.get(str(zcode), str(zcode))


# ---------------------------------------------------------------------------
# 운영사(CPO)명 정규화
# ---------------------------------------------------------------------------
# 공개데이터의 busi_nm은 같은 사업자가 법인격 표기((주)/㈜/주식회사), 옛/새 브랜드,
# 오탈자·자릿수 잘림 등으로 여러 이름으로 흩어져 있다. 집계 정확도를 위해
# 계산 시점에 대표명으로 통합한다(원본 DB는 건드리지 않는 비파괴 방식).

# 법인격 표기 (사명 어디에 있든 제거)
_CPO_LEGAL = re.compile(r"주식회사|유한회사|\(주\)|\(유\)|\(사\)|\(재\)|㈜")
# 사명 끝의 괄호 주석: (SP)/(sp)/(민수)/(위탁운영)/(evPlug) 등
_CPO_TRAIL_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_CPO_WS = re.compile(r"\s+")


def _cpo_base(name: str) -> str:
    """법인격·괄호주석·공백을 제거한 기준형. (한글 사명은 공백 없는 게 표준)"""
    s = (name or "").strip()
    s = _CPO_LEGAL.sub("", s)
    s = _CPO_TRAIL_PAREN.sub("", s)
    s = _CPO_WS.sub("", s)
    return s.strip()


# 기준형(_cpo_base 결과) → 대표명. 규칙만으로 안 합쳐지는 의미적 병합만 여기 둔다.
CPO_ALIASES = {
    # 에바 (자사) — 세 갈래를 하나로
    "EVAR": "에바",
    # GS차지비 (구 '차지비' 리브랜드)
    "차지비": "GS차지비",
    # 파워큐브
    "파워큐브코리아": "파워큐브",
    "파워큨브코리아": "파워큐브",   # 오탈자
    # LG유플러스 (볼트업 브랜드 포함)
    "LG유플러스볼트업": "LG유플러스",
    "엘지유플러스": "LG유플러스",
    "앨지유플러스": "LG유플러스",   # 오탈자
    # 타디스테크놀로지 (evPlug 브랜드)
    "evPlug": "타디스테크놀로지",
    # 엘에스이링크 (영문 표기 병합)
    "LSE-Link": "엘에스이링크",
    # 스타코프 (사명 중복 표기)
    "스타코프스타코프": "스타코프",
    # SK
    "SK일렉링": "SK일렉링크",       # 자릿수 잘림
    "sk시그넷": "SK시그넷",         # 대소문자
    # 현대오일뱅크
    "HD현대오일뱅크": "현대오일뱅크",
    # 한국홈충전
    "한국홈충전기렌탈": "한국홈충전",
    # SG생활안전
    "에스지생활안전": "SG생활안전",
    # 엘쓰리일렉트릭파워
    "L3일렉트릭파워": "엘쓰리일렉트릭파워",
    # 펌프킨
    "펌프킨,KGICT": "펌프킨",
    # 자릿수 잘림 (사용자 확인 병합)
    "이카플럭": "이카플러그",
    "캐스트프": "캐스트프로",
    "한국전기차인프라기": "한국전기차인프라기술",
    "모던": "모던텍",
    "이지차": "이지차저",
    "이브이시": "이브이시스",
    # 한국전력공사
    "한국전력공사": "한국전력",
    "한국전력1": "한국전력",
}


def normalize_cpo(name: str) -> str:
    """운영사명을 대표명으로 정규화. 미상/빈값은 원문 유지."""
    base = _cpo_base(name)
    if not base:
        return (name or "").strip()
    return CPO_ALIASES.get(base, base)

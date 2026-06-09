"""환경설정 및 도메인 상수 정의."""
import os

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

# Turso(호스팅 SQLite) — 설정 시 로컬 SQLite 대신 클라우드 DB 사용
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

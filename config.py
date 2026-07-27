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
# 충전소 유형 (kind 대분류 / kindDetail 상세)
# ---------------------------------------------------------------------------
# API의 kind/kindDetail 코드. 공식 활용가이드 코드표를 못 구해, 전국 실제 충전소명
# (아파트/골프장/호텔 등)으로 역추론한 라벨. 대체로 정확하나 일부(★)는 추정.
KIND_NAMES = {
    "A0": "공공기관",
    "B0": "주차장",
    "C0": "고속도로 휴게소",
    "D0": "문화·관광",
    "E0": "판매·숙박·근생",
    "F0": "자동차시설",
    "G0": "업무·기타",
    "H0": "공동주택",
    "I0": "의료·복지·종교",
    "J0": "교육·체육·문화기관",
}

KIND_DETAIL_NAMES = {
    # A0 공공기관
    "A001": "관공서(청사·소방)",
    "A002": "읍·면·동 주민센터",
    "A003": "공공기관·공단",
    "A004": "공공 문화·체육시설",
    # B0 주차장
    "B001": "공영주차장",
    "B002": "공원 주차장",
    "B003": "주차장",
    "B004": "노외·기타 주차장",
    # C0 휴게소
    "C001": "고속도로 휴게소",
    "C002": "휴게소·만남의광장",
    "C003": "졸음쉼터·관광휴게소",
    # D0 문화·관광
    "D001": "공원(국립·생태)",
    "D002": "컨벤션·전시·기념관",
    "D003": "민속마을·체험",
    "D004": "수목원",
    "D005": "전시판매장",
    "D006": "관광안내소·동굴",
    "D007": "관광지 주차장",
    "D008": "박물관",
    "D009": "유적지",
    # E0 판매·숙박·근생
    "E001": "마트·판매점",
    "E002": "백화점·대형쇼핑",
    "E003": "숙박(호텔·리조트)",
    "E004": "골프장·체육레저",
    "E005": "카페",
    "E006": "음식점",
    "E007": "주유소·충전소",
    "E008": "영화관",
    # F0 자동차시설
    "F001": "자동차 판매·전시장",
    "F002": "자동차 정비소",
    # G0 업무·기타
    "G002": "자연휴양림·캠핑장",
    "G003": "마을회관·경로당",
    "G004": "업무·공공시설",
    "G005": "오피스텔",
    "G006": "오피스텔·주택",
    # H0 공동주택
    "H001": "아파트",
    "H002": "연립·다세대",
    "H003": "기타 주거·시설",
    "H004": "기숙사·직원숙소",
    "H005": "연립주택·주택",
    # I0 의료·복지·종교
    "I001": "병원",
    "I002": "종교시설",
    "I003": "보건소",
    "I004": "경찰서",
    "I005": "도서관",
    "I006": "복지·문화복지회관",
    "I007": "청소년수련관",
    "I008": "은행·금융",
    # J0 교육·체육·문화기관
    "J001": "대학·학교",
    "J002": "연수원·교육원",
    "J004": "예술회관·공연장",
    "J005": "기념관·문화촌",
    "J006": "연구원",
    "J007": "경기장·운동장",
}


def kind_name(code: str) -> str:
    """kind 대분류 코드 → 라벨. 미상은 '기타'."""
    c = (code or "").strip()
    if not c:
        return "(미분류)"
    return KIND_NAMES.get(c, f"기타({c})")


def kind_detail_name(code: str) -> str:
    """kindDetail 상세 코드 → 라벨. 미상은 코드 그대로."""
    c = (code or "").strip()
    if not c:
        return "(미분류)"
    return KIND_DETAIL_NAMES.get(c, c)


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

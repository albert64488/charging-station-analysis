"""data.go.kr 한국환경공단 전기차 충전소 정보 API 클라이언트.

- getChargerInfo   : 충전소·충전기 메타 + 현재 상태 (전수). 메타 시드용.
- getChargerStatus : 최근 period(분) 내 상태 변경분 (델타). 반복 폴링용.

zcode/zscode를 비우면 전국. 응답은 XML, 페이지네이션으로 수집.
"""
import time
import xml.etree.ElementTree as ET

import requests

import config


_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _fetch_page(operation, page_no, num_of_rows, zcode=None, zscode=None, period=None):
    url = f"{config.API_BASE}/{operation}"
    params = {
        "serviceKey": config.DATAGO_SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
    }
    if zcode:
        params["zcode"] = zcode
    if zscode:
        params["zscode"] = zscode
    if period is not None:
        params["period"] = period

    last_err = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=config.HTTP_TIMEOUT)
            if resp.status_code in _TRANSIENT_STATUS:
                last_err = requests.exceptions.HTTPError(
                    f"{resp.status_code} (page {page_no})")
            else:
                resp.raise_for_status()
                return resp.text
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
        # 점증 백오프 후 재시도 (마지막 시도면 생략)
        if attempt < config.MAX_RETRIES - 1:
            time.sleep(config.RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError(f"페이지 {page_no} 수집 실패(재시도 {config.MAX_RETRIES}회): {last_err}")


def _parse(xml_text):
    """(items, total_count) 반환. 서비스 오류 시 RuntimeError."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"응답 파싱 실패(XML 아님). 앞부분: {xml_text[:200]!r}") from e

    if root.tag.endswith("OpenAPI_ServiceResponse"):
        msg = root.findtext(".//errMsg") or ""
        auth = root.findtext(".//returnAuthMsg") or ""
        code = root.findtext(".//returnReasonCode") or ""
        raise RuntimeError(f"API 오류 [{code}] {msg} {auth}".strip())

    code_el = root.findtext(".//resultCode")
    if code_el not in (None, "", "00", "0"):
        msg = root.findtext(".//resultMsg") or ""
        raise RuntimeError(f"API 오류 [{code_el}] {msg}")

    items = [{c.tag: (c.text or "").strip() for c in item} for item in root.findall(".//item")]
    total_text = root.findtext(".//totalCount")
    total = int(total_text) if total_text and total_text.isdigit() else None
    return items, total


def _fetch_all(operation, zcode=None, zscode=None, period=None, num_of_rows=None):
    if not config.DATAGO_SERVICE_KEY:
        raise RuntimeError("DATAGO_SERVICE_KEY가 설정되지 않았습니다. .env를 확인하세요.")
    num = num_of_rows or config.NUM_OF_ROWS
    all_items, page = [], 1
    while True:
        xml_text = _fetch_page(operation, page, num, zcode, zscode, period)
        items, total = _parse(xml_text)
        all_items.extend(items)
        if not items or len(items) < num:
            break
        if total is not None and len(all_items) >= total:
            break
        page += 1
        time.sleep(0.3)
    return all_items


def fetch_charger_info(zcode=None, zscode=None, num_of_rows=None):
    """전수 조회(메타+상태). zcode/zscode 비우면 전국."""
    return _fetch_all("getChargerInfo", zcode=zcode, zscode=zscode, num_of_rows=num_of_rows)


def fetch_charger_status(period=10, zcode=None, zscode=None, num_of_rows=None):
    """최근 period(분, 최대 10) 내 상태 변경분. zcode/zscode 비우면 전국."""
    return _fetch_all("getChargerStatus", zcode=zcode, zscode=zscode,
                      period=period, num_of_rows=num_of_rows)

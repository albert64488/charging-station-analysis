# 충전소 추정 이용률 분석 시스템 (초기버전)

무공해차/한국환경공단 충전기 상태 데이터를 주기적으로 수집하여
**시간 기준 추정 이용률·가동률**을 산출하고, 공유 가능한 웹 대시보드로 보여준다.

> 초기버전 범위: **수집 + 계산 + 공유용 뷰**. 입지분석/ROI/외부데이터 연계는 다음 단계.

## 산정 정의

| 지표 | 정의 |
|------|------|
| 이용률 | 충전중 관측수 ÷ 전체 관측수 × 100 |
| 가동률 | (사용가능 + 충전중) 관측수 ÷ 전체 관측수 × 100 |
| 장애율 | (통신이상 + 운영중지 + 점검중 + 미확인) ÷ 전체 관측수 × 100 |
| 충전소 이용률 | 충전기 이용률의 **출력(kW) 가중평균**(권장) 또는 단순평균 |

상태코드: `1` 통신이상 · `2` 사용가능 · `3` 충전중 · `4` 운영중지 · `5` 점검중 · `9` 미확인

## 구조

```
charging-station-analysis/
├── config.py            # 환경설정·도메인 상수(상태코드/지역코드/급속분류)
├── run_collect.py       # 수집 실행 엔트리포인트
├── app.py               # Streamlit 대시보드(공유용 뷰)
├── src/
│   ├── api_client.py    # data.go.kr getChargerInfo 호출·파싱
│   ├── db.py            # SQLite 스키마/저장
│   ├── collector.py     # 수집→upsert→관측 insert
│   ├── calculator.py    # 이용률/가동률 계산(pandas)
│   └── sample_data.py   # 키 없이 검증용 샘플 생성기
└── data/charging.db     # SQLite (자동 생성)
```

## 설치

```powershell
cd charging-station-analysis
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 빠른 시작 (API 키 없이)

샘플 과거 7일치 데이터를 넣고 대시보드를 띄운다.

```powershell
python run_collect.py --backfill 7
streamlit run app.py
```

## 실데이터 수집 (API 키 보유 시)

1. [공공데이터포털](https://www.data.go.kr) 로그인 →
   **"한국환경공단_전기자동차 충전소 정보"** 활용신청
2. 발급된 **일반 인증키(Decoding)** 를 `.env`에 입력:

   ```powershell
   Copy-Item .env.example .env
   # .env 파일 열어서 DATAGO_SERVICE_KEY=... 채우고, ZCODE로 지역 지정
   ```

3. 수집 실행:

   ```powershell
   python run_collect.py            # 1회 스냅샷
   ```

### 10분 주기 자동 수집

MD 사양대로 10분 간격(하루 144회)으로 누적하려면 OS 스케줄러 사용:

- **Windows 작업 스케줄러**: 10분마다 `python run_collect.py` 실행 등록
- 또는 임시로 PowerShell 루프:

  ```powershell
  while ($true) { python run_collect.py; Start-Sleep -Seconds 600 }
  ```

> 이용률은 **누적 관측수가 충분할수록** 정확해진다(예: 7일 × 144회 ≈ 1,008관측).

## 다음 단계 (MD 로드맵)

- [ ] 입지 분석: 후보지 반경 2km 평균/최대/최소 이용률, 밀집도, 운영사 수
- [ ] 지도 Heat Map · 운영사 필터 고도화
- [ ] 교통량·생활인구·전기차 등록대수·상권 데이터 결합 → 예상 매출/BEP/ROI

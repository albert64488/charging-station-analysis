# 무료 24/7 클라우드 배포 가이드

내 PC가 꺼져 있어도 수집되도록 **GitHub Actions(수집) + Turso(데이터) + Streamlit Cloud(대시보드)** 로 올린다. 전부 무료.

```
GitHub Actions ──(10분마다 poll / 하루4회 refresh)──▶ Turso(호스팅 SQLite)
                                                          │
                                          Streamlit Cloud(대시보드)가 읽음
```

---

## STEP 1. GitHub 저장소 만들고 코드 올리기

1. github.com → 우측 상단 **+** → **New repository** → 이름 예: `charging-station-analysis` → **Private** 선택 → Create.
2. 내 PC에서 PowerShell 열고 프로젝트 폴더에서:

```powershell
cd C:\Users\이건희\charging-station-analysis
git init
git add .
git commit -m "초기 버전: 충전소 이용률 분석"
git branch -M main
git remote add origin https://github.com/<내아이디>/charging-station-analysis.git
git push -u origin main
```

> `.gitignore`가 `data/`, `.env`를 제외하므로 **DB·키는 올라가지 않음**(안전).

---

## STEP 2. Turso(무료 호스팅 DB) 만들기

1. **turso.tech** 접속 → **Sign up** → GitHub 계정으로 로그인.
2. 대시보드에서 **Create Database** → 이름 예: `charging` → 지역은 가까운 곳(예: `nrt` 도쿄) 선택.
3. 만든 DB 클릭 → 다음 두 값을 복사해 둔다:
   - **Database URL** (`libsql://charging-<org>.turso.io` 형태)
   - **Auth Token** — `Create Token`(또는 Connect 화면)에서 발급 → 긴 문자열 복사

> CLI를 선호하면: `turso db create charging` → `turso db show charging --url` (URL), `turso db tokens create charging` (토큰).

---

## STEP 3. GitHub Secrets 등록 (키를 코드에 안 넣고 안전하게)

저장소 → **Settings** → 좌측 **Secrets and variables** → **Actions** → **New repository secret** 로 3개 등록:

| Name | Value |
|------|-------|
| `DATAGO_SERVICE_KEY` | data.go.kr 일반 인증키 |
| `TURSO_DATABASE_URL` | STEP 2의 Database URL |
| `TURSO_AUTH_TOKEN` | STEP 2의 Auth Token |

---

## STEP 4. 첫 전수 시드 실행 (수동)

1. 저장소 → **Actions** 탭 → (처음이면 워크플로 활성화 버튼 클릭) → 왼쪽 **collect** 선택.
2. 우측 **Run workflow** → mode = **refresh** → **Run workflow**.
3. 몇 분 후 초록 체크 ✓ 면 성공. 전국 51만 충전기가 Turso에 시드됨.

이후엔 **자동**으로:
- 10분마다 `poll`(변경분 누적)
- 하루 4회 `refresh`(현재상태 재동기화 = 구멍 보정)

> Actions 무료 시간: Private 저장소는 월 2,000분. poll 1회 ~1분이라 충분. (빠듯하면 cron을 `*/15`로 늘리면 됨)

---

## STEP 5. 대시보드를 Streamlit Cloud에 올리기 (공유 URL)

1. **share.streamlit.io** → GitHub 로그인 → **New app**.
2. Repository = 내 저장소, Branch = `main`, Main file path = `app.py` → **Deploy**.
3. 앱 화면 우측 메뉴 **Settings → Secrets** 에 아래 입력(TOML 형식):

```toml
TURSO_DATABASE_URL = "libsql://charging-<org>.turso.io"
TURSO_AUTH_TOKEN = "여기에_토큰"
```

4. 저장하면 자동 재시작 → **`https://<앱이름>.streamlit.app`** 주소로 누구나(또는 지정한 사람만) 접속.

끝! 이제 내 PC와 무관하게 24시간 수집되고, 대시보드는 항상 최신 데이터를 보여준다.

---

## 참고 / 문제 해결

- **이용률이 처음엔 비어 보임** — poll이 며칠 쌓여야 충전중 시간이 누적돼 정밀해진다. refresh 직후엔 현재상태 기준 추정만 보임.
- **GitHub Actions 타이밍** — cron은 정확히 10분을 보장하지 않음(지연·누락 가능). 그래서 하루 4회 refresh로 보정한다.
- **로컬 테스트** — `.env`에 `TURSO_*`를 넣으면 로컬에서도 Turso에 붙고, 비워두면 기존처럼 `data/charging.db`(로컬)로 동작.
- **libsql 설치 오류 시** — Actions 로그 확인 후 패키지 버전을 알려주면 맞춰 조정.

"""공유용 Streamlit 대시보드 — 시간(지속구간) 기반 이용률/가동률.

실행:  streamlit run app.py
"""
import datetime
import os

import pandas as pd
import streamlit as st

# Streamlit Cloud: secrets → 환경변수 (config가 env에서 읽도록). config import 前에 설정.
for _k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "DATAGO_SERVICE_KEY"):
    try:
        if _k in st.secrets:
            os.environ.setdefault(_k, str(st.secrets[_k]))
    except Exception:
        pass

import config
from src import calculator, db

st.set_page_config(page_title="충전소 추정 이용률 분석", layout="wide")

# 이용률/가동률을 막대 게이지로 보여주기 위한 컬럼 설정
PCT = lambda label: st.column_config.ProgressColumn(label, format="%.1f%%", min_value=0, max_value=100)
COLCFG = {"이용률": PCT("이용률"), "장애율": PCT("장애율")}

st.title("⚡ 충전소 추정 이용률 분석")
st.caption("한국환경공단 충전기 상태 데이터 · 상태 변경 이벤트 기반 시간 점유율(이용률·가동률)")

db.init_db()


@st.cache_data(ttl=300)
def _filters():
    with db.get_conn() as conn:
        zcodes = [r[0] for r in conn.execute(
            "SELECT DISTINCT zcode FROM stations WHERE zcode IS NOT NULL AND zcode <> '' ORDER BY zcode")]
        dmin = conn.execute(
            "SELECT MIN(d) FROM ("
            "  SELECT MIN(start_dt) d FROM state_intervals"
            "  UNION ALL SELECT MIN(since_dt) d FROM current_state)"
        ).fetchone()[0]
    return zcodes, dmin


@st.cache_data(ttl=300, show_spinner="데이터 불러오는 중… (전국은 최초 1회 30~40초 소요)")
def _load(zcode, start, end):
    return calculator.load_durations(zcode=zcode, start=start, end=end)


zcodes, dmin = _filters()
if not zcodes or not dmin:
    st.warning("수집된 데이터가 없습니다. 먼저 터미널에서 실행하세요:\n\n"
               "`python run_collect.py seed-sample --days 7`  (샘플)\n\n"
               "`python run_collect.py refresh` → `python run_collect.py poll`  (실데이터)")
    st.stop()

# ---------------- 사이드바 필터 ----------------
with st.sidebar:
    st.header("🔎 필터")
    zopts = {"전국 (전체)": None}
    zopts.update({f"{config.zcode_name(z)} ({z})": z for z in zcodes})
    zcode = zopts[st.selectbox("지역", list(zopts.keys()))]

    dmin_d = pd.to_datetime(dmin).date()
    today = datetime.date.today()
    drange = st.date_input("기간", value=(dmin_d, today), min_value=dmin_d, max_value=today)
    d0, d1 = drange if isinstance(drange, tuple) and len(drange) == 2 else (drange, drange)
    start_s, end_s = f"{d0} 00:00:00", f"{d1} 23:59:59"

    chargers_all = _load(zcode, start_s, end_s)
    cpo_opts = sorted(chargers_all["busi_nm"].dropna().replace("", pd.NA).dropna().unique()) \
        if not chargers_all.empty else []
    sel_cpos = st.multiselect("운영사 (CPO)", cpo_opts, placeholder="전체 (선택 시 해당 CPO만)")

    type_sel = st.radio("충전기 구분", ["전체", "급속", "완속"], horizontal=True)
    method = "weighted" if st.radio("충전소·CPO 집계", ["출력 가중평균 (권장)", "단순평균"]).startswith("출력") else "simple"

# ---------------- 필터 적용 ----------------
chargers = chargers_all.copy()
if not chargers.empty:
    if sel_cpos:
        chargers = chargers[chargers["busi_nm"].isin(sel_cpos)]
    if type_sel != "전체":
        chargers = chargers[chargers["충전기구분"] == type_sel]

if chargers.empty:
    st.info("선택한 조건에 해당하는 데이터가 없습니다. 필터를 조정해 주세요.")
    st.stop()

stations = calculator.station_summary(chargers, method=method)
cpos = calculator.cpo_summary(chargers, method=method)

# ---------------- 상단 KPI ----------------
k = st.columns(5)
k[0].metric("충전소 수", f"{stations['충전소명'].nunique():,}")
k[1].metric("충전기 수", f"{len(chargers):,}")
k[2].metric("운영사 수", f"{chargers['busi_nm'].nunique():,}")
k[3].metric("평균 이용률", f"{chargers['이용률'].mean():.1f}%")
k[4].metric("관측시간 합", f"{chargers['관측시간(h)'].sum():,.0f} h")

st.divider()
tab_cpo, tab_station, tab_map = st.tabs(["🏷️ 운영사(CPO) 비교", "🏢 충전소·충전기", "🗺️ 지도"])

# ===== 탭 1: CPO 비교 =====
with tab_cpo:
    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("운영사별 운영현황")
        st.dataframe(
            cpos, width="stretch", hide_index=True, column_config=COLCFG,
        )
    with c2:
        st.subheader("CPO 이용률 비교")
        top = cpos.sort_values("이용률", ascending=False).head(12).set_index("운영사(CPO)")
        st.bar_chart(top["이용률"])
    bt = st.columns(2)
    bt[0].subheader("CPO별 충전기 규모")
    bt[0].bar_chart(cpos.sort_values("충전기수", ascending=False).head(12).set_index("운영사(CPO)")["충전기수"])
    bt[1].subheader("급속 vs 완속 평균 이용률")
    by_type = chargers.groupby("충전기구분")["이용률"].mean().round(1)
    m = bt[1].columns(2)
    m[0].metric("급속", f"{by_type.get('급속', float('nan')):.1f}%")
    m[1].metric("완속", f"{by_type.get('완속', float('nan')):.1f}%")

# ===== 탭 2: 충전소·충전기 =====
with tab_station:
    st.subheader("충전소별 이용률")
    st.dataframe(
        stations[["충전소명", "운영사", "이용률", "장애율", "충전기수", "급속", "완속", "관측시간(h)"]],
        width="stretch", hide_index=True, column_config=COLCFG,
    )
    st.subheader("충전소 상세 — 실시간 충전기 상태")
    sel = st.selectbox("충전소 선택", stations["충전소명"].tolist())
    sel_id = stations.loc[stations["충전소명"] == sel, "stat_id"].iloc[0]

    info, live = calculator.live_status(sel_id)
    st.markdown(
        f"**🔌 {info['stat_nm']}**　|　운영사 **{info['busi_nm']}**　|　📍 {info['addr']}")

    def _color(v):
        v = str(v)
        if "충전중" in v:
            return "background-color:#d6f5d6;color:#147a14"   # 초록
        if v == "사용가능":
            return "background-color:#dbe9ff;color:#1456c4"   # 파랑
        return "background-color:#fde2e1;color:#c0392b"       # 빨강(장애 등)

    sty = live.style
    try:
        sty = sty.map(_color, subset=["현재상태"])
    except AttributeError:
        sty = sty.applymap(_color, subset=["현재상태"])
    st.dataframe(sty, width="stretch", hide_index=True)
    st.caption("‘충전중’ 경과시간 = 현재시각 − 마지막 상태변경 시각(갱신일시). 수집 주기(10분)만큼 지연될 수 있음.")

    with st.expander("📊 기간 집계 보기 (이용률·장애율)"):
        agg = chargers[chargers["stat_id"] == sel_id][
            ["chger_id", "충전기구분", "output", "이용률", "장애율", "관측시간(h)"]
        ].rename(columns={"chger_id": "충전기ID", "output": "출력(kW)"})
        st.dataframe(agg, width="stretch", hide_index=True, column_config=COLCFG)

# ===== 탭 3: 지도 =====
with tab_map:
    st.subheader("충전소 위치")
    MAX_PTS = 5000
    geo = chargers[["stat_id", "lat", "lng"]].dropna().drop_duplicates("stat_id")
    geo = geo.rename(columns={"lat": "latitude", "lng": "longitude"})
    if not geo.empty:
        if len(geo) > MAX_PTS:
            shown = geo.sample(MAX_PTS, random_state=0)
            st.caption(f"표시 {MAX_PTS:,}개 (전체 {len(geo):,}개 중 샘플 — 지도 성능 위해)")
        else:
            shown = geo
            st.caption(f"표시 충전소 {len(geo):,}개")
        st.map(shown[["latitude", "longitude"]])
    else:
        st.caption("위치(위경도) 데이터가 없습니다.")

st.caption("이용률 = 충전중 시간 / 전체 관측시간 · "
           "장애율 = (통신이상+운영중지+점검중+미확인) 시간 / 전체")

"""공유용 Streamlit 대시보드 — 정적 스냅샷(집계 부분합) 기반 이용률/장애율.

앱은 라이브 DB를 보지 않는다. 수집기가 발행한 스냅샷(snapshot.db)을 내려받아 읽는다.
→ DB 다운/콜드스타트/용량 문제로부터 앱이 분리됨. 전국 집계도 부분합이라 가볍다.

실행:  streamlit run app.py
로컬:  SNAPSHOT_DB=data/snapshot.db streamlit run app.py
"""
import pandas as pd
import streamlit as st

import config
from src import calculator, db, snapshot

st.set_page_config(page_title="충전소 추정 이용률 분석", layout="wide")

PCT = lambda label: st.column_config.ProgressColumn(label, format="%.1f%%", min_value=0, max_value=100)
COLCFG = {"이용률": PCT("이용률"), "장애율": PCT("장애율")}


def _status_color(v):
    v = str(v)
    if "충전중" in v:
        return "background-color:#d6f5d6;color:#147a14"   # 초록
    if v == "사용가능":
        return "background-color:#dbe9ff;color:#1456c4"   # 파랑
    return "background-color:#fde2e1;color:#c0392b"       # 빨강(장애 등)


def render_station_detail(stat_id, key_prefix=""):
    """선택/검색한 충전소의 이용률 + 실시간 상태 + 반경 입지분석 (탭 공용)."""
    import pydeck as pdk

    info, live = calculator.live_status(stat_id)
    st.markdown(
        f"**🔌 {info.get('stat_nm', '')}**　|　운영사 **{info.get('busi_nm', '')}**　|　📍 {info.get('addr', '')}")

    ch = calculator.load_durations(stat_id=stat_id)
    if ch is not None and not ch.empty:
        m = st.columns(5)
        m[0].metric("평균 이용률", f"{ch['이용률'].mean():.1f}%")
        m[1].metric("충전기 수", f"{len(ch)}")
        m[2].metric("급속 / 완속",
                    f"{int((ch['is_fast'] == 1).sum())} / {int((ch['is_fast'] == 0).sum())}")
        m[3].metric("충전시간(충전기당)", f"{ch['충전시간(h)'].mean():.0f} h")
        m[4].metric("관측시간(충전기당)", f"{ch['관측시간(h)'].mean():.0f} h")

    st.markdown("**실시간 충전기 상태**")
    if live is None or live.empty:
        st.info("실시간 상태 데이터가 아직 없습니다.")
    else:
        sty = live.style
        try:
            sty = sty.map(_status_color, subset=["현재상태"])
        except AttributeError:
            sty = sty.applymap(_status_color, subset=["현재상태"])
        st.dataframe(sty, width="stretch", hide_index=True)
        st.caption("'충전중' 경과시간 = 현재시각 − 마지막 상태변경 시각(갱신일시). 수집 주기(10분)만큼 지연될 수 있음.")

    if ch is not None and not ch.empty:
        with st.expander("📊 충전기별 이용률·장애율"):
            detail = ch[["chger_id", "충전기구분", "output", "이용률", "장애율",
                         "충전시간(h)", "관측시간(h)"]].rename(
                columns={"chger_id": "충전기ID", "output": "출력(kW)"})
            st.dataframe(detail, width="stretch", hide_index=True, column_config=COLCFG)

    # 📍 반경 입지 분석 (체크 시에만 계산 — 밀집지역은 무거움)
    st.divider()
    if st.checkbox("📍 주변 입지 분석 보기 (반경 내 경쟁 충전소 지도)", key=f"{key_prefix}_show"):
        radius = st.slider("반경(km)", 1.0, 5.0, 3.0, 0.5, key=f"{key_prefix}_radius")
        center, near = _nearby(stat_id, radius)
        if center is None:
            st.caption("이 충전소의 위치 정보가 없어 주변 분석을 할 수 없어요.")
        elif near.empty:
            st.caption(f"반경 {radius}km 내 충전소가 없습니다.")
        else:
            n = st.columns(4)
            n[0].metric("반경 내 충전소", f"{len(near)}곳")
            n[1].metric("평균 이용률",
                        f"{near['이용률'].mean():.1f}%" if near['이용률'].notna().any() else "-")
            n[2].metric("운영사 수", f"{near['운영사'].nunique()}")
            n[3].metric("급속 / 완속",
                        f"{int(near['급속'].fillna(0).sum())} / {int(near['완속'].fillna(0).sum())}")
            nm = near.copy()
            nm["이용률"] = nm["이용률"].fillna(0)
            nm["color"] = nm["이용률"].apply(
                lambda u: [int(255 * min(u, 100) / 100), int(200 * (1 - min(u, 100) / 100)), 90, 200])
            layer = pdk.Layer("ScatterplotLayer", data=nm, get_position=["longitude", "latitude"],
                              get_fill_color="color", get_radius=130, pickable=True)
            ctr = pdk.Layer("ScatterplotLayer",
                            data=pd.DataFrame([{"longitude": center[1], "latitude": center[0]}]),
                            get_position=["longitude", "latitude"],
                            get_fill_color=[0, 90, 255, 255], get_radius=200)
            st.pydeck_chart(pdk.Deck(
                layers=[layer, ctr],
                initial_view_state=pdk.ViewState(latitude=center[0], longitude=center[1], zoom=13),
                tooltip={"text": "{충전소명}\n이용률 {이용률}%"}))
            st.caption("🔵 검색한 충전소 · 점 색상: 🔴 이용률 높음 → 🟢 낮음")
            st.dataframe(
                near[["충전소명", "운영사", "거리(km)", "이용률", "장애율", "충전기수", "급속", "완속"]],
                width="stretch", hide_index=True, column_config=COLCFG)


@st.cache_data(ttl=300, show_spinner="주변 충전소 분석 중…")
def _nearby(stat_id, radius):
    return calculator.nearby_stations(stat_id, radius)


# ---------------- 스냅샷 준비 (라이브 DB 미사용) ----------------
@st.cache_data(ttl=600, show_spinner="스냅샷 불러오는 중…")
def _snapshot():
    """최대 10분마다 최신 스냅샷 확인·다운로드. (경로, 생성시각) 반환."""
    return snapshot.ensure()


st.title("⚡ 충전소 추정 이용률 분석")
st.caption("한국환경공단 충전기 상태 데이터 · 상태 변경 이벤트 기반 시간 이용률")

try:
    snap_path, snap_at = _snapshot()
except snapshot.SnapshotUnavailable as e:
    st.warning("아직 스냅샷이 발행되지 않았어요. 수집기가 첫 스냅샷을 올리면 표시됩니다.\n\n"
               f"({e})")
    st.stop()
snapshot._use(snap_path)  # 매 rerun마다 config를 로컬 스냅샷으로 고정


@st.cache_data(ttl=600)
def _zcodes(_at):
    with db.get_conn() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT zcode FROM station_stats "
            "WHERE zcode IS NOT NULL AND zcode <> '' ORDER BY zcode")]


@st.cache_data(ttl=600, show_spinner="집계 불러오는 중…")
def _load_partials(zcode, _at):
    return calculator.load_partials(zcode)


zcodes = _zcodes(snap_at)
if not zcodes:
    st.warning("스냅샷에 집계 데이터가 없습니다.")
    st.stop()

st.caption(f"🕐 스냅샷 생성: **{snap_at or '알 수 없음'}** (KST · 약 10분마다 갱신)")

# ---------------- 사이드바 필터 ----------------
with st.sidebar:
    st.header("🔎 필터")
    # 전국 통합 + 시도별 (전국도 부분합이라 가볍게 처리)
    zopts = {"🇰🇷 전국 통합": None}
    zopts.update({f"{config.zcode_name(z)} ({z})": z for z in zcodes})
    zsel = st.selectbox("지역", list(zopts.keys()))
    zcode = zopts[zsel]

    part_all = _load_partials(zcode, snap_at)
    cpo_opts = sorted(part_all["busi_nm"].dropna().replace("", pd.NA).dropna().unique()) \
        if not part_all.empty else []
    sel_cpos = st.multiselect("운영사 (CPO)", cpo_opts, placeholder="전체 (선택 시 해당 CPO만)")

    # 충전소 유형 (대분류 → 상세). 스냅샷에 유형 없으면(구버전) 숨김.
    sel_kind = sel_detail = None
    kinds_present = (sorted(part_all["kind"].dropna().replace("", pd.NA).dropna().unique())
                     if ("kind" in part_all.columns and not part_all.empty) else [])
    if kinds_present:
        kopts = {"전체": None}
        kopts.update({config.kind_name(k): k for k in kinds_present})
        sel_kind = kopts[st.selectbox("충전소 유형", list(kopts.keys()))]
        if sel_kind is not None:
            sub = part_all[part_all["kind"] == sel_kind]
            dpres = sorted(sub["kind_detail"].dropna().replace("", pd.NA).dropna().unique())
            dopts = {"전체 (상세)": None}
            dopts.update({config.kind_detail_name(d): d for d in dpres})
            sel_detail = dopts[st.selectbox("상세 유형", list(dopts.keys()))]

    type_sel = st.radio("충전기 구분", ["전체", "급속", "완속"], horizontal=True)
    method = "weighted" if st.radio("충전소·CPO 집계", ["출력 가중평균 (권장)", "단순평균"]).startswith("출력") else "simple"

# ---------------- 필터 적용 ----------------
part = part_all
if not part.empty:
    if sel_cpos:
        part = part[part["busi_nm"].isin(sel_cpos)]
    if sel_kind is not None:
        part = part[part["kind"] == sel_kind]
        if sel_detail is not None:
            part = part[part["kind_detail"] == sel_detail]
    part_cpo = part.copy()          # 타입 필터 전 (급속/완속 비교용)
    if type_sel == "급속":
        part = part[part["is_fast"] == 1]
    elif type_sel == "완속":
        part = part[part["is_fast"] == 0]
else:
    part_cpo = part

if part.empty:
    st.info("선택한 조건에 해당하는 데이터가 없습니다. 필터를 조정해 주세요.")
    st.stop()

stations = calculator.summarize_stations(part, method=method)
cpos = calculator.summarize_cpos(part, method=method)
kpi = calculator.kpi_from_partials(part, method=method)

# ---------------- 상단 KPI ----------------
k = st.columns(5)
k[0].metric("충전소 수", f"{kpi['충전소수']:,}")
k[1].metric("충전기 수", f"{kpi['충전기수']:,}")
k[2].metric("운영사 수", f"{kpi['운영사수']:,}")
k[3].metric("평균 이용률", f"{kpi['평균이용률']:.1f}%")
k[4].metric("관측시간(충전기당)", f"{kpi['관측시간']:,.0f} h")

st.divider()
tab_search, tab_cpo, tab_station, tab_map = st.tabs(
    ["🔍 충전소 검색", "🏷️ 운영사(CPO) 비교", "🏢 충전소·충전기", "🗺️ 지도"])

# ===== 탭 0: 충전소 검색 (전국, 지역 무관) =====
with tab_search:
    st.subheader("충전소 이름·주소로 검색 (전국)")
    term = st.text_input("충전소명/주소 입력", placeholder="예: 반포써밋, 강동구 명일로, 스타필드",
                         key="search_term", label_visibility="collapsed")
    if term and term.strip():
        results = calculator.search_stations(term.strip())
        if results.empty:
            st.info(f"'{term}' 검색 결과가 없습니다.")
        else:
            st.caption(f"{len(results)}곳 검색됨(최대 100). 행을 클릭하면 실시간 상태가 표시됩니다.")
            ev = st.dataframe(
                results[["충전소명", "운영사", "주소"]],
                width="stretch", hide_index=True,
                on_select="rerun", selection_mode="single-row", key="search_table")
            rsel = ev.selection.rows
            if rsel and rsel[0] < len(results):
                st.divider()
                render_station_detail(results.iloc[rsel[0]]["stat_id"], key_prefix="search")
            else:
                st.caption("⬆️ 위 목록에서 충전소를 클릭하세요.")
    else:
        st.caption("충전소 이름 또는 주소 일부를 입력하면 전국에서 찾습니다. "
                   "(플랫폼마다 충전소명이 다를 수 있어 주소로 찾는 게 정확할 때가 많아요)")

# ===== 탭 1: CPO 비교 =====
with tab_cpo:
    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("운영사별 운영현황")
        st.dataframe(cpos, width="stretch", hide_index=True, column_config=COLCFG)
    with c2:
        st.subheader("CPO 이용률 비교")
        top = cpos.sort_values("이용률", ascending=False).head(12).set_index("운영사(CPO)")
        st.bar_chart(top["이용률"])
    bt = st.columns(2)
    bt[0].subheader("CPO별 충전기 규모")
    bt[0].bar_chart(cpos.sort_values("충전기수", ascending=False).head(12).set_index("운영사(CPO)")["충전기수"])
    bt[1].subheader("급속 vs 완속 평균 이용률")
    by_type = calculator.type_util(part_cpo, method=method)
    m = bt[1].columns(2)
    m[0].metric("급속", f"{by_type.get('급속', float('nan')):.1f}%")
    m[1].metric("완속", f"{by_type.get('완속', float('nan')):.1f}%")

# ===== 탭 2: 충전소·충전기 =====
with tab_station:
    st.subheader("충전소별 이용률")
    st.caption("👉 행을 클릭하면 아래에 해당 충전소의 실시간 충전기 상태가 표시됩니다.")
    _event = st.dataframe(
        stations[["충전소명", "운영사", "유형", "이용률", "장애율", "충전기수", "급속", "완속", "충전시간(h)", "관측시간(h)"]],
        width="stretch", hide_index=True, column_config=COLCFG,
        on_select="rerun", selection_mode="single-row", key="station_table",
    )
    _rows = _event.selection.rows
    if len(stations):
        sel_idx = _rows[0] if (_rows and _rows[0] < len(stations)) else 0
        sel_id = stations.iloc[sel_idx]["stat_id"]
        st.subheader("충전소 상세 — 실시간 충전기 상태")
        render_station_detail(sel_id, key_prefix="station")

# ===== 탭 3: 지도 =====
with tab_map:
    st.subheader("충전소 위치")
    MAX_PTS = 5000
    geo = stations[["stat_id", "lat", "lng"]].dropna().drop_duplicates("stat_id")
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

st.caption("이용률 = 충전중 시간 / 전체 관측시간")
st.caption("장애율 = (통신이상+운영중지+점검중+미확인) 시간 / 전체 관측시간")

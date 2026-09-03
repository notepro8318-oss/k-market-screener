import streamlit as st

from market_dashboard import approx_universe_pbr, build_dashboard

st.title("🚦 시장 진입 타이밍")
st.caption("코스피·코스닥 전체 시장의 '지금이 저점권인가'를 확인하는 대시보드 — 개별 종목이 아닌 시장 전체 환경을 봅니다.")
st.info(
    "충족 지표 4개 이상(코스피 PBR 포함) → **적극 진입** / 3개 이상 → **정상 진입** / 2개 이하 → **진입 보류**",
    icon="ℹ️",
)

st.subheader("① 코스피 PBR 입력")
approx_pbr = approx_universe_pbr()
if approx_pbr is not None:
    st.caption(
        f"참고: 자체 종목 캐시(흑자 종목만 포함) 시가총액가중평균 PBR ≈ {approx_pbr}배 "
        "— 적자·부실주가 빠져 있어 실제 코스피 PBR보다 높게 나오는 편향이 있으니 판정에는 쓰지 않습니다."
    )
pbr_input = st.number_input(
    "오늘자 코스피 선행/확정 PBR (KRX 정보데이터시스템·증권사 HTS/MTS 등에서 확인)",
    min_value=0.0, max_value=5.0, value=st.session_state.get("market_pbr_input", 0.0), step=0.01,
    key="market_pbr_input",
    help="자동 수집 API가 없어 직접 입력이 필요합니다. 0.00이면 미입력으로 간주해 판정에서 제외합니다.",
)
pbr_value = pbr_input if pbr_input > 0 else None

refresh = st.button("🔄 시장 데이터 새로고침", type="primary")


@st.cache_data(ttl=1800, show_spinner="시장 데이터를 가져오는 중...")
def _cached_dashboard(pbr):
    return build_dashboard(pbr_input=pbr)


if refresh:
    _cached_dashboard.clear()

rows, summary = _cached_dashboard(pbr_value)

st.subheader("② 판정 결과")
verdict = summary["판정"]
verdict_color = {"적극 진입": "🟢", "정상 진입": "🟡", "진입 보류": "🔴"}[verdict]
col1, col2, col3 = st.columns(3)
col1.metric("충족 지표", f"{summary['충족_개수']} / {summary['전체_지표수']}")
col2.metric("데이터 없음", summary["데이터없음_개수"])
col3.metric("판정", f"{verdict_color} {verdict}")
if summary["데이터없음_개수"] > 0:
    st.caption("데이터를 가져오지 못한 지표는 충족 개수 계산에서 제외했습니다 (➖ 표시).")

st.subheader("③ 지표 상세")


def _icon(met):
    if met is None:
        return "➖"
    return "✅" if met else "❌"


import pandas as pd

detail_df = pd.DataFrame([
    {
        "구분": r["구분"], "지표": r["지표"],
        "값": f"{r['값']}{r['단위']}" if r["값"] is not None else "데이터 없음",
        "기준": r["기준"], "충족": _icon(r["충족"]), "설명": r["설명"],
    }
    for r in rows
])
st.dataframe(detail_df, use_container_width=True, hide_index=True)
st.caption(
    "MDD는 코스피/코스닥 중 하나만 기준을 충족해도 인정합니다. "
    "CNN Fear & Greed는 비공식 API라 간헐적으로 조회에 실패할 수 있습니다 (그 경우 판정에서 제외)."
)

import streamlit as st

from market_dashboard import build_dashboard, load_kospi_pbr_cache

st.title("🚦 시장 진입 타이밍")
st.caption("코스피·코스닥 전체 시장의 '지금이 저점권인가'를 확인하는 대시보드 — 개별 종목이 아닌 시장 전체 환경을 봅니다.")
st.info(
    "충족 지표 4개 이상(코스피 PBR 포함) → **적극 진입** / 3개 이상 → **정상 진입** / 2개 이하 → **진입 보류**",
    icon="ℹ️",
)

st.subheader("① 코스피 PBR")
pbr_cached, pbr_date = load_kospi_pbr_cache()
if pbr_cached is not None:
    st.caption(f"indexergo.com 크롤링 캐시: {pbr_cached}배 ({pbr_date} 기준, crawl_kospi_pbr.py로 매일 자동 갱신)")
else:
    msg = f"크롤링 캐시가 오래됐습니다(마지막 {pbr_date}) - 직접 입력해주세요." if pbr_date \
        else "크롤링 캐시가 아직 없습니다 - 직접 입력해주세요."
    st.warning(msg)
pbr_override_input = st.number_input(
    "직접 입력으로 덮어쓰기 (선택)", min_value=0.0, max_value=5.0, value=0.0, step=0.01,
    help="0.00으로 두면 위 크롤링 캐시 값을 그대로 사용합니다. 캐시가 없거나 오래됐을 때만 채워주세요.",
)
pbr_override = pbr_override_input if pbr_override_input > 0 else None

refresh = st.button("🔄 시장 데이터 새로고침", type="primary")


@st.cache_data(ttl=1800, show_spinner="시장 데이터를 가져오는 중...")
def _cached_dashboard(pbr_override):
    return build_dashboard(pbr_override=pbr_override)


if refresh:
    _cached_dashboard.clear()

rows, summary = _cached_dashboard(pbr_override)

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

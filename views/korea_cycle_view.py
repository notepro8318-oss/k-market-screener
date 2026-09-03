import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from korea_cycle import build_korea_cycle
from market_dashboard import fetch_index_history

st.title("🌐 한국 경제사이클")
st.caption("글로벌 수요 → 한국 수출 → 국내 공장 → 금융/환율 순서로 경기 신호가 실제로 전달되는지 4단계로 확인합니다.")
st.info(
    "원래 설계된 8개 지표 중 2개(美 ISM 스프레드, 中 차이신 PMI)는 무료 공식 API가 없어 대체지표로 교체했습니다 "
    "(자세한 내용은 하단 참고). '비반도체 수출'·'재고순환지표' 단독 통계는 이번 버전에 포함되지 않았습니다.",
    icon="ℹ️",
)

_STATUS_COLOR = {True: "#22c55e", False: "#ef4444", "partial": "#eab308", None: "#9ca3af"}
_STATUS_ICON = {True: "✅", False: "❌", "partial": "🟡", None: "➖"}
_STATUS_LABEL = {True: "강세", False: "약세", "partial": "부분 확장", None: "데이터 없음"}


def _gauge(met, size=90):
    color = _STATUS_COLOR[met]
    fig = go.Figure(go.Pie(
        values=[100, 0], hole=0.7,
        marker=dict(colors=[color, "rgba(148,163,184,0.2)"], line=dict(width=0)),
        textinfo="none", sort=False,
    ))
    fig.update_layout(
        showlegend=False, margin=dict(l=8, r=8, t=8, b=8), height=size,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(text=_STATUS_ICON[met], x=0.5, y=0.5, showarrow=False, font=dict(size=size * 0.34))],
    )
    return fig


import os

try:
    ecos_key = st.secrets["ECOS_API_KEY"]
except Exception:
    ecos_key = os.environ.get("ECOS_API_KEY")

if not ecos_key:
    st.error(
        "ECOS_API_KEY가 설정되어 있지 않습니다. Streamlit Cloud라면 앱 설정의 Secrets에 "
        "`ECOS_API_KEY = \"...\"`를 추가해주세요. 로컬이라면 환경변수로 설정하세요."
    )
    st.stop()


@st.cache_data(ttl=3600, show_spinner="한국 경제사이클 데이터를 가져오는 중...")
def _cached_layers(key):
    fx = fetch_index_history("USD/KRW", years=1)
    return build_korea_cycle(key, fx_df=fx)


refresh = st.button("🔄 새로고침", type="primary")
if refresh:
    _cached_layers.clear()

layers = _cached_layers(ecos_key)

arrows = ["↓ 수주 전달", "↓ 생산 반영", "↓ 밸류에이션/자금 반영"]
for i, layer in enumerate(layers):
    verdict = layer["판정"]
    with st.container(border=True):
        top_col, gauge_col = st.columns([4, 1])
        with top_col:
            st.markdown(f"**{layer['레이어']}**")
            for ind in layer["지표"]:
                val = ind["값"] if ind["값"] is not None else "데이터 없음"
                st.markdown(f"- {ind['이름']}: **{val}** (기준: {ind['기준']})")
            st.markdown(
                f"<span style='color:{_STATUS_COLOR[verdict]};font-weight:700'>"
                f"{_STATUS_ICON[verdict]} {_STATUS_LABEL[verdict]}</span>", unsafe_allow_html=True,
            )
        with gauge_col:
            st.plotly_chart(_gauge(verdict), use_container_width=True,
                             config={"displayModeBar": False}, key=f"layer_gauge_{i}")
        with st.expander("강세/약세 신호 기준"):
            st.caption(f"강세: {layer['강세신호']}")
            st.caption(f"약세/주의: {layer['약세신호']}")
    if i < len(layers) - 1:
        st.markdown(f"<div style='text-align:center;color:#9ca3af'>{arrows[i]}</div>", unsafe_allow_html=True)

st.divider()
with st.expander("📋 대체지표 사용 근거"):
    st.markdown(
        "- **美 ISM 신규주문-재고 스프레드 → 내구재 신규수주(DGORDER) YoY - 제조업 재고(MNFCTRIMSA) YoY**: "
        "FRED가 2016년 ISM 데이터를 라이선스 문제로 완전히 제거해 무료 소스가 없음. "
        "같은 개념(주문 모멘텀 대비 재고 축적 속도)을 서베이 대신 실물 하드데이터로 근사.\n"
        "- **中 차이신 제조업 PMI → 국가통계국(NBS) 공식 제조업 PMI**: 차이신도 유료 라이선스라 무료 API 없음. "
        "다만 NBS는 국유기업 비중이 높아 차이신(민간·중소기업 위주)과 표본 성격이 다름을 감안해야 함.\n"
        "- **비반도체 수출**: 한국은행 ECOS에 반도체 제외 수출 단독 통계가 없어 이번 버전에서는 총 수출 YoY로 단순화.\n"
        "- **재고순환지표**: 통계청 KOSIS 신규 API 키 등록이 필요해 이번 버전에서는 제외. "
        "다만 선행지수 순환변동치 자체가 재고순환지표를 구성요소 중 하나로 포함하고 있음."
    )

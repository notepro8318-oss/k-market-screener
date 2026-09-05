import math
import os
from collections import defaultdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from korea_cycle import build_korea_cycle, compute_quarterly_cycle_phase
from market_dashboard import fetch_index_history

st.title("🌐 한국 경제사이클")
st.caption("글로벌 수요 → 한국 수출 → 국내 공장 → 금융/환율 순서로 경기 신호가 실제로 전달되는지 4단계로 확인합니다.")

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


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_quarters(key):
    return compute_quarterly_cycle_phase(key, n_quarters=4)


refresh = st.button("🔄 새로고침", type="primary")
if refresh:
    _cached_layers.clear()
    _cached_quarters.clear()

# --- 분기별 경기국면 위치 차트 (Fidelity 비즈니스 사이클 스타일) ---

_PHASE_BAR = [("Early", 0.0, 0.24), ("Mid", 0.24, 0.55), ("Late", 0.55, 0.80), ("Recession", 0.80, 1.0)]
_PHASE_SLOT = {"Early": (0.02, 0.22), "Mid": (0.26, 0.53), "Late": (0.57, 0.78), "Recession": (0.82, 0.98)}


def _cycle_curve_y(x, x_peak=0.55, x_zero=0.80, dip_amp=0.35):
    """
    Recovery -> Expansion -> Contraction 개형을 흉내낸 순수 장식용 곡선.
    실제 데이터 값이 아니라 국면(Early/Mid/Late/Recession) 상의 위치만 의미를 가진다.
    """
    if x <= x_peak:
        return math.sin((math.pi / 2) * (x / x_peak))
    if x <= x_zero:
        return math.cos((math.pi / 2) * (x - x_peak) / (x_zero - x_peak))
    t = (x - x_zero) / (1 - x_zero)
    return -dip_amp * math.sin(math.pi * t)


def _assign_dot_x(quarters):
    groups = defaultdict(list)
    for i, q in enumerate(quarters):
        groups[q["국면"]].append(i)
    xs = [0.5] * len(quarters)
    for phase, idxs in groups.items():
        lo, hi = _PHASE_SLOT.get(phase, (0.4, 0.6))
        step = (hi - lo) / (len(idxs) + 1)
        for rank, i in enumerate(idxs, start=1):
            xs[i] = lo + step * rank
    return xs


def _amber_shades(n):
    dark, light = (138, 75, 18), (245, 166, 35)
    if n <= 1:
        return ["#{:02x}{:02x}{:02x}".format(*light)]
    return [
        "#{:02x}{:02x}{:02x}".format(*[
            round(dark[j] + (light[j] - dark[j]) * i / (n - 1)) for j in range(3)
        ])
        for i in range(n)
    ]


def _cycle_position_chart(quarters):
    xs = _assign_dot_x(quarters)
    colors = _amber_shades(len(quarters))
    curve_x = np.linspace(0, 1, 300)
    x_zero = 0.80
    main_x = curve_x[curve_x <= x_zero]
    tail_x = curve_x[curve_x >= x_zero]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.16, 0.68, 0.16], vertical_spacing=0.04,
    )

    grad_x = np.linspace(0, 1, 100)
    grad_scale = [[0, "#22c55e"], [0.5, "#eab308"], [1, "#ef4444"]]
    for row in (1, 3):
        fig.add_trace(go.Heatmap(
            z=[grad_x], x=grad_x, y=[0], colorscale=grad_scale, showscale=False, hoverinfo="skip",
        ), row=row, col=1)

    for name, lo, hi in _PHASE_BAR:
        fig.add_shape(
            type="rect", x0=lo, x1=hi, y0=1.05, y1=1.35,
            fillcolor="#5b7a9d", line=dict(color="white", width=1), row=2, col=1,
        )
        fig.add_annotation(
            x=(lo + hi) / 2, y=1.2, text=name, showarrow=False,
            font=dict(color="white", size=15, family="Arial Black"), row=2, col=1,
        )

    fig.add_trace(go.Scatter(
        x=main_x, y=[_cycle_curve_y(x) for x in main_x], mode="lines",
        line=dict(color="#6b7280", width=3), hoverinfo="skip",
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=tail_x, y=[_cycle_curve_y(x) for x in tail_x], mode="lines",
        line=dict(color="#ef4444", width=3), hoverinfo="skip",
    ), row=2, col=1)
    fig.add_shape(
        type="line", x0=0, x1=1, y0=0, y1=0,
        line=dict(color="#9ca3af", width=1, dash="dot"), row=2, col=1,
    )

    n = len(quarters)
    for i, (q, x, color) in enumerate(zip(quarters, xs, colors)):
        is_last = i == n - 1
        fig.add_trace(go.Scatter(
            x=[x], y=[_cycle_curve_y(x)], mode="markers+text" if is_last else "markers",
            marker=dict(size=38 if is_last else 18, color=color, line=dict(color="white", width=2)),
            text=[q["분기"].split()[0]] if is_last else None,
            textfont=dict(color="white", size=16, family="Arial Black"),
            hovertext=f"{q['분기']}: {q['국면']} (선행지수 순환변동치 {q['값']})", hoverinfo="text",
        ), row=2, col=1)

    fig.update_xaxes(range=[0, 1], showticklabels=False, showgrid=False, zeroline=False)
    fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False)
    fig.update_yaxes(range=[-0.5, 1.5], row=2, col=1)
    fig.update_layout(
        showlegend=False, height=460, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


quarters = _cached_quarters(ecos_key)
with st.container(border=True):
    st.caption("🌡️ 인플레이션 압력 (초록=낮음 → 빨강=높음, 개념 참고용)")
    if quarters:
        st.plotly_chart(_cycle_position_chart(quarters), use_container_width=True, config={"displayModeBar": False})
        chip_colors = _amber_shades(len(quarters))
        chips = "".join(
            f"<span style='display:inline-block;width:12px;height:12px;background:{c};"
            f"border-radius:2px;margin:0 4px 0 14px;vertical-align:middle'></span>"
            f"<span style='vertical-align:middle;font-size:0.85rem'>{q['분기']}</span>"
            for q, c in zip(quarters, chip_colors)
        )
        st.markdown(f"<div style='text-align:center'><b>🇰🇷 한국</b>{chips}</div>", unsafe_allow_html=True)
    else:
        st.info("분기별 경기국면을 계산하기에 ECOS 선행지수 데이터가 충분하지 않습니다.")
    st.caption("📊 경기민감자산 상대 성과 (초록=강세 → 빨강=약세, 개념 참고용)")
    st.caption(
        "선행지수 순환변동치(ECOS 901Y067)의 수준·모멘텀으로 분기별 경기국면(Early/Mid/Late/Recession)을 "
        "분류한 결과입니다. 곡선 모양은 국면 이해를 돕기 위한 장식용이며 실제 수치 그래프가 아닙니다."
    )

st.divider()

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


st.info(
    "원래 설계된 8개 지표 중 2개(美 ISM 스프레드, 中 차이신 PMI)는 무료 공식 API가 없어 대체지표로 교체했습니다. "
    "'비반도체 수출'만 관세청/KOSIS 별도 키가 필요해 총 수출 YoY로 대체 중입니다(자세한 내용은 하단 참고). "
    "'재고순환지표'는 이번 업데이트로 3번 레이어에 추가됐습니다.",
    icon="ℹ️",
)

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
        "- **비반도체 수출**: 한국은행 ECOS와 통계청 KOSIS Open API를 모두 확인했지만 MTI/HS 품목별 세부 수출액 "
        "시계열은 없음 - ECOS는 총괄/국가별/대륙별만 제공하고, KOSIS의 '품목별 수입·수출현황'(관세청 기업무역활동통계, "
        "DT_134006_N001)은 이름과 달리 수출기업 수·생존율 등 기업 단위 구조통계라 반도체 수출 '금액'과는 무관함. "
        "실제 MTI 품목별 수출액(반도체 등)은 관세청이 별도 운영하는 tradedata.go.kr(수출입무역통계, 10일 단위 잠정치 포함) "
        "포털에만 있고, 이 포털의 Open API 신청 경로는 아직 확인하지 못해 현재는 총 수출 YoY로 단순화된 상태를 유지함.\n"
        "- **재고순환지표**: ECOS 901Y032(산업별 생산·출하·재고 지수)의 총지수 출하/재고 원지수로 "
        "'출하 YoY - 재고 YoY'를 계산 - 통계청이 실제 쓰는 정의와 동일하며, 기존 ECOS 키만으로 3번 레이어에 "
        "반영됩니다(대체지표가 아닌 실제 지표).\n"
        "- **분기별 경기국면 위치(상단 차트)**: 선행지수 순환변동치(기준=100, 이미 추세제거된 값)의 수준과 "
        "전분기 대비 모멘텀만으로 국면을 분류합니다(OECD의 경기순환시계 방식과 동일한 논리). "
        "인플레이션 압력·자산 성과 그라데이션 바는 참고용 개념도이며 특정 지표에 연동되지 않습니다."
    )

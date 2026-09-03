import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from market_dashboard import build_dashboard, fetch_index_history, load_kospi_pbr_cache

st.title("🚦 시장 진입 타이밍")
st.caption("코스피·코스닥 전체 시장의 '지금이 저점권인가'를 확인하는 대시보드 — 개별 종목이 아닌 시장 전체 환경을 봅니다.")

_STATUS_COLOR = {True: "#22c55e", False: "#ef4444", None: "#9ca3af"}
_STATUS_ICON = {True: "✅", False: "❌", None: "➖"}
_VERDICT_COLOR = {"적극 진입": "#22c55e", "정상 진입": "#eab308", "진입 보류": "#ef4444"}


def _gauge(fill_pct, met, size=110, center_text=None, ring_width=0.28):
    color = _STATUS_COLOR[met]
    pct = fill_pct if fill_pct is not None else 0
    fig = go.Figure(go.Pie(
        values=[pct, 100 - pct], hole=1 - ring_width,
        marker=dict(colors=[color, "rgba(148,163,184,0.20)"], line=dict(width=0)),
        textinfo="none", sort=False, direction="clockwise", rotation=0,
    ))
    fig.update_layout(
        showlegend=False, margin=dict(l=0, r=0, t=0, b=0), width=size, height=size,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(
            text=center_text if center_text is not None else _STATUS_ICON[met],
            x=0.5, y=0.5, showarrow=False, font=dict(size=size * (0.19 if center_text else 0.34)),
        )],
    )
    return fig


def _hex_to_rgba(hex_color, alpha):
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _sparkline(df, color):
    close = df["Close"]
    fig = go.Figure(go.Scatter(
        x=close.index, y=close.values, mode="lines", line=dict(color=color, width=2), fill="tozeroy",
        fillcolor=_hex_to_rgba(color, 0.12),
    ))
    fig.update_layout(
        showlegend=False, margin=dict(l=0, r=0, t=4, b=0), height=80,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig


cache_meta_col, refresh_col = st.columns([5, 1])
with refresh_col:
    refresh = st.button("🔄 새로고침", type="primary", use_container_width=True)

with st.expander("⚙️ 코스피 PBR 수동 보정 (평소엔 필요 없음)"):
    pbr_cached, pbr_date = load_kospi_pbr_cache()
    if pbr_cached is not None:
        st.caption(f"indexergo.com 크롤링 캐시: {pbr_cached}배 ({pbr_date} 기준, 매일 자동 갱신)")
    else:
        msg = f"크롤링 캐시가 오래됐습니다(마지막 {pbr_date}) - 직접 입력해주세요." if pbr_date \
            else "크롤링 캐시가 아직 없습니다 - 직접 입력해주세요."
        st.warning(msg)
    pbr_override_input = st.number_input(
        "직접 입력으로 덮어쓰기 (0.00 = 크롤링 값 사용)", min_value=0.0, max_value=5.0, value=0.0, step=0.01,
    )
pbr_override = pbr_override_input if pbr_override_input > 0 else None


@st.cache_data(ttl=1800, show_spinner="시장 데이터를 가져오는 중...")
def _cached_dashboard(pbr_override):
    return build_dashboard(pbr_override=pbr_override)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_trend():
    return fetch_index_history("KS11", years=1), fetch_index_history("KQ11", years=1)


if refresh:
    _cached_dashboard.clear()
    _cached_trend.clear()

rows, summary = _cached_dashboard(pbr_override)
verdict = summary["판정"]

with cache_meta_col:
    st.caption(f"데이터 조회 시각 기준 최신값 · 충족 {summary['충족_개수']}/{summary['전체_지표수']} · 판정 **{verdict}**")

left, right = st.columns([1, 2.3], gap="medium")

with left:
    with st.container(border=True):
        st.markdown("**종합 판정**")
        st.plotly_chart(
            _gauge(
                summary["충족_개수"] / summary["전체_지표수"] * 100, None, size=190,
                center_text=f"{summary['충족_개수']}/{summary['전체_지표수']}",
            ).update_traces(marker=dict(colors=[_VERDICT_COLOR[verdict], "rgba(148,163,184,0.20)"])),
            use_container_width=False, config={"displayModeBar": False}, key="summary_gauge",
        )
        st.markdown(
            f"<div style='text-align:center;font-size:1.3rem;font-weight:700;color:{_VERDICT_COLOR[verdict]}'>"
            f"{verdict}</div>", unsafe_allow_html=True,
        )
        st.caption("충족 4개 이상(PBR 포함)=적극 진입 · 3개 이상=정상 진입 · 2개 이하=진입 보류")
        if summary["데이터없음_개수"] > 0:
            st.caption(f"➖ 데이터 없음 {summary['데이터없음_개수']}개는 판정에서 제외")

    kospi_hist, kosdaq_hist = _cached_trend()
    with st.container(border=True):
        st.markdown("**최근 1년 지수 추이**")
        if kospi_hist is not None:
            st.caption("코스피")
            st.plotly_chart(_sparkline(kospi_hist, "#3b82f6"), use_container_width=True,
                             config={"displayModeBar": False}, key="kospi_spark")
        if kosdaq_hist is not None:
            st.caption("코스닥")
            st.plotly_chart(_sparkline(kosdaq_hist, "#f97316"), use_container_width=True,
                             config={"displayModeBar": False}, key="kosdaq_spark")

with right:
    card_cols = st.columns(4, gap="small")
    for i, r in enumerate(rows):
        with card_cols[i % 4]:
            with st.container(border=True):
                st.caption(r["구분"])
                st.markdown(f"**{r['지표']}**")
                val_text = f"{r['값']}{r['단위']}" if r["값"] is not None else "데이터 없음"
                st.markdown(f"<div style='font-size:1.05rem;font-weight:600'>{val_text}</div>", unsafe_allow_html=True)
                st.plotly_chart(
                    _gauge(r.get("fill_pct"), r["충족"]), use_container_width=False,
                    config={"displayModeBar": False}, key=f"gauge_{i}",
                )
                st.caption(r["기준"])
        if i % 4 == 3 and i != len(rows) - 1:
            card_cols = st.columns(4, gap="small")

st.divider()
with st.expander("📋 지표 상세 (기준·설명 포함 전체 표)"):
    detail_df = pd.DataFrame([
        {
            "구분": r["구분"], "지표": r["지표"],
            "값": f"{r['값']}{r['단위']}" if r["값"] is not None else "데이터 없음",
            "기준": r["기준"], "충족": _STATUS_ICON[r["충족"]], "설명": r["설명"],
        }
        for r in rows
    ])
    st.dataframe(detail_df, use_container_width=True, hide_index=True)
    st.caption(
        "MDD·RSI는 코스피/코스닥 또는 일봉/주봉 중 하나만 기준을 충족해도 인정합니다. "
        "CNN Fear & Greed는 비공식 API라 간헐적으로 조회에 실패할 수 있습니다(그 경우 판정에서 제외). "
        "카드의 게이지 채움 정도는 실제 판정과 별개로 기준선까지의 근접도를 보여주는 참고용 시각화입니다."
    )

import datetime

import streamlit as st

from backtest import run_backtest
from screener import DEFAULT_FILTER_CRITERIA
from ui_common import get_dart_api_key

st.title("🧪 조건별 백테스트")
st.caption("매년 4/1(전년도 사업보고서 공시 마감 직후) 리밸런싱하는 과거 시뮬레이션")

with st.expander("⚠️ 데이터 한계 (결과 해석 시 꼭 참고하세요)", expanded=False):
    st.markdown(
        "- 대상 유니버스는 **현재** 시가총액 상위 N종목으로 제한합니다. "
        "과거에 상장폐지된 종목은 포함되지 않아 **생존편향(survivorship bias)**이 존재합니다.\n"
        "- 발행주식수는 현재 값을 과거 시점에도 동일하게 근사 적용합니다.\n"
        "- PER/PBR은 실시간 시세가 아니라 **(해당 시점 추정 시가총액 ÷ DART 재무제표)**로 직접 계산한 값입니다.\n"
        "- 리밸런싱은 연 1회(사업보고서 기준)만 지원합니다."
    )

with st.sidebar:
    st.header("⚙️ 백테스트 조건")

    dart_api_key = get_dart_api_key()

    current_year = datetime.datetime.today().year
    col1, col2 = st.columns(2)
    with col1:
        start_year = st.number_input("시작 연도(사업보고서)", min_value=2015, max_value=current_year, value=2018, step=1)
    with col2:
        end_year = st.number_input("종료 연도(사업보고서)", min_value=2015, max_value=current_year, value=min(2025, current_year), step=1)

    universe_size = st.slider("대상 유니버스 (현재 시가총액 상위 N종목)", min_value=100, max_value=1000, value=500, step=50)
    benchmark = st.selectbox("벤치마크 지수", options=["KOSPI", "KOSDAQ"], index=0)

    st.divider()

    min_marcap_eok = st.number_input(
        "최소 시가총액 (억원)", min_value=0,
        value=DEFAULT_FILTER_CRITERIA["MIN_MARCAP"] // 100_000_000, step=100,
    )
    max_per = st.number_input("최대 PER", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MAX_PER"], step=0.5)
    max_pbr = st.number_input("최대 PBR", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MAX_PBR"], step=0.1)
    min_opm = st.number_input("최소 영업이익률 (%)", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MIN_OPM"], step=0.5)
    min_roa = st.number_input("최소 ROA (%)", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MIN_ROA"], step=0.5)
    min_roe = st.number_input("최소 ROE (%)", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MIN_ROE"], step=0.5)
    min_trading_val_eok = st.number_input(
        "최소 20일 평균 거래대금 (억원)", min_value=0.0,
        value=DEFAULT_FILTER_CRITERIA["MIN_TRADING_VALUE_20D"] / 100_000_000, step=0.5,
    )
    min_fscore = st.slider("최소 F-Score", min_value=0, max_value=9, value=DEFAULT_FILTER_CRITERIA["MIN_FSCORE"])

    criteria = {
        "MIN_MARCAP": min_marcap_eok * 100_000_000,
        "MAX_PER": max_per,
        "MAX_PBR": max_pbr,
        "MIN_OPM": min_opm,
        "MIN_ROA": min_roa,
        "MIN_ROE": min_roe,
        "MIN_TRADING_VALUE_20D": min_trading_val_eok * 100_000_000,
        "MIN_FSCORE": min_fscore,
    }

    use_cache = st.checkbox("동일 조건 결과 캐시 사용", value=True, help="같은 조건으로 이미 실행한 적이 있으면 재계산 없이 즉시 불러옵니다.")

    run_clicked = st.button("🧪 백테스트 실행", type="primary", use_container_width=True)

if run_clicked:
    if not dart_api_key:
        st.error("OpenDART API Key를 입력해주세요.")
        st.stop()
    if start_year > end_year:
        st.error("시작 연도가 종료 연도보다 늦을 수 없습니다.")
        st.stop()

    status_box = st.empty()
    progress_bar = st.progress(0.0)

    def log(msg):
        text = str(msg).strip()
        if text:
            status_box.info(text)

    def progress_cb(stage, done, total):
        progress_bar.progress(done / total if total else 0.0)

    with st.spinner("백테스트 실행 중... (유니버스/기간에 따라 수 분~수십 분 소요될 수 있습니다)"):
        result = run_backtest(
            dart_api_key, criteria, int(start_year), int(end_year),
            universe_size=int(universe_size), benchmark=benchmark,
            log=log, progress_cb=progress_cb, use_cache=use_cache,
        )

    progress_bar.empty()
    status_box.empty()

    df_yearly = result["yearly"]
    df_curve = result["equity_curve"]

    if df_yearly.empty:
        st.warning("리밸런싱 가능한 기간이 없습니다. 연도 범위를 확인해주세요.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "포트폴리오 CAGR",
            f"{result['cagr']*100:.2f}%" if result["cagr"] is not None else "N/A",
        )
        c2.metric(
            f"{benchmark} CAGR",
            f"{result['benchmark_cagr']*100:.2f}%" if result["benchmark_cagr"] is not None else "N/A",
        )
        c3.metric(
            "MDD (완료 구간 기준)",
            f"{result['mdd']*100:.2f}%" if result["mdd"] is not None else "N/A",
        )

        if not df_curve.empty:
            st.subheader("누적 수익률 (완료된 리밸런싱 구간만 반영)")
            st.line_chart(df_curve.set_index("사업보고서연도"))

        st.subheader("연도별 결과")
        st.dataframe(df_yearly, use_container_width=True, hide_index=True)

        st.subheader("연도별 선정 종목 상세")
        for year, df_hold in result["holdings_by_year"].items():
            with st.expander(f"{year}년 사업보고서 기준 선정 종목 ({len(df_hold)}개)"):
                if df_hold.empty:
                    st.write("조건을 만족하는 종목이 없습니다.")
                else:
                    st.dataframe(df_hold, use_container_width=True, hide_index=True)

        csv_bytes = df_yearly.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 연도별 결과 CSV 다운로드",
            data=csv_bytes,
            file_name="backtest_yearly_results.csv",
            mime="text/csv",
        )
else:
    st.info("왼쪽에서 조건을 설정한 뒤 **백테스트 실행** 버튼을 눌러주세요.")

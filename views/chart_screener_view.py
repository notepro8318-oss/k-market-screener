import pandas as pd
import streamlit as st

from chart_screener import load_chart_screener_cache

st.title("📊 차트 분석 종목 찾기")
st.caption("월봉 추세(5개월 이동평균선)와 거래량·거래대금 급증을 함께 보는 차트 기반 스크리닝")

cache = load_chart_screener_cache()
if cache is None:
    st.error(
        "캐시 데이터가 아직 없습니다. 코스피·코스닥 전 종목의 일봉을 매번 새로 받아야 해서 "
        "Streamlit에서 실시간으로 돌리기엔 너무 느려, 로컬 환경에서 `python crawl_chart_screener.py`를 "
        "실행해 `data/chart_screener_cache.json`을 만든 뒤 커밋/푸시해야 합니다."
    )
    st.stop()

st.caption(
    f"데이터 기준일: {cache['generated_at']} (종목 {cache['count']}개) — "
    "거래량/거래대금 급증은 그날그날 새로 나오는 신호라 매일 자동 갱신됩니다."
)

with st.sidebar:
    st.header("⚙️ 조건")

    st.caption("추세 조건")
    min_consecutive_months = st.slider(
        "월봉 종가가 5개월선 위에서 연속으로 머문 개월 수 (이상)",
        min_value=1, max_value=12, value=3,
        help="월봉 종가가 5개월 이동평균선 위에 있었던 가장 최근부터의 연속 개월 수(이번달 포함) 기준",
    )

    st.caption("급증 조건 (최근 거래일 vs 직전 20거래일 평균)")
    min_volume_ratio = st.number_input(
        "거래량 배수 (이상)", min_value=1.0, value=3.0, step=0.5,
    )
    min_value_ratio = st.number_input(
        "거래대금 배수 (이상)", min_value=1.0, value=3.0, step=0.5,
    )

    st.caption("제외 필터")
    max_rise_from_low_pct = st.number_input(
        "최근 3개월 저점 대비 상승률 (%) 이상이면 제외 (이미 크게 오른 상태)",
        min_value=0.0, value=30.0, step=5.0,
    )
    min_trading_value_eok = st.number_input(
        "급증 신호일 거래대금 (억원) 미만이면 제외 (절대금액이 작은 노이즈성 급증 배제)",
        min_value=0.0, value=1500.0, step=100.0,
    )

    market = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True, key="chart_market")

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True, key="chart_run")

if run_clicked:
    df = pd.DataFrame(cache["rows"])
    if market != "전체":
        df = df[df["시장구분"] == market]

    required_cols = ["5개월선_연속상회월수", "거래량_배수", "거래대금_배수", "3개월저점대비_상승률(%)", "당일_거래대금"]
    df = df.dropna(subset=required_cols)

    min_trading_value = min_trading_value_eok * 100_000_000
    cond = (
        (df["5개월선_연속상회월수"] >= min_consecutive_months)
        & (df["거래량_배수"] >= min_volume_ratio)
        & (df["거래대금_배수"] >= min_value_ratio)
        & (df["3개월저점대비_상승률(%)"] < max_rise_from_low_pct)
        & (df["당일_거래대금"] >= min_trading_value)
    )
    df_screened = df[cond].copy()
    if not df_screened.empty:
        df_screened = df_screened.sort_values("거래대금_배수", ascending=False)
        df_screened["_종목명_plain"] = df_screened["종목명"]
        df_screened["종목명"] = df_screened.apply(
            lambda r: f"https://finance.naver.com/item/main.naver?code={r['종목코드']}#{r['_종목명_plain']}",
            axis=1,
        )
    st.session_state["chart_screening_df"] = df_screened

df_final = st.session_state.get("chart_screening_df")

if df_final is None:
    st.info("왼쪽에서 조건을 설정한 뒤 **스크리닝 실행** 버튼을 눌러주세요.")
elif df_final.empty:
    st.warning("조건을 모두 만족하는 종목이 없습니다. 조건을 완화한 뒤 다시 시도해보세요.")
else:
    st.success(f"{len(df_final)}개 종목이 조건을 통과했습니다.")
    st.caption("거래대금 배수(급증 정도) 높은 순으로 정렬되어 있습니다.")
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "종목코드", "종목명", "시장구분", "현재가",
            "5개월선_연속상회월수", "거래량_배수", "거래대금_배수",
            "당일_거래대금", "3개월저점대비_상승률(%)", "기준일",
        ],
        column_config={
            "종목코드": st.column_config.TextColumn(
                "종목코드", help="한국거래소(KRX) 상장 종목 코드 (6자리)",
            ),
            "종목명": st.column_config.LinkColumn(
                "종목명", help="클릭하면 네이버증권 해당 종목 페이지로 이동합니다", display_text=r"#(.*)$",
            ),
            "시장구분": st.column_config.TextColumn(
                "시장구분", help="상장 시장 (KOSPI 또는 KOSDAQ)",
            ),
            "현재가": st.column_config.NumberColumn(
                "현재가", help="가장 최근 거래일 종가 (원)",
            ),
            "5개월선_연속상회월수": st.column_config.NumberColumn(
                "5개월선 연속상회(개월)",
                help="월봉 종가가 5개월 이동평균선 위에 있었던, 가장 최근 달(이번달 포함)부터의 연속 개월 수",
            ),
            "거래량_배수": st.column_config.NumberColumn(
                "거래량 배수", format="%.1f배",
                help="최근 거래일 거래량 ÷ 직전 20거래일 평균 거래량",
            ),
            "거래대금_배수": st.column_config.NumberColumn(
                "거래대금 배수", format="%.1f배",
                help="최근 거래일 거래대금 ÷ 직전 20거래일 평균 거래대금",
            ),
            "당일_거래대금": st.column_config.NumberColumn(
                "당일 거래대금(원)", help="가장 최근 거래일의 거래대금 (종가 × 거래량으로 근사)",
            ),
            "3개월저점대비_상승률(%)": st.column_config.NumberColumn(
                "3개월저점 대비(%)", format="%.1f%%",
                help="최근 3개월 저점 대비 현재가 상승률. 너무 높으면 이미 크게 오른 뒤의 급증으로 보고 제외 대상",
            ),
            "기준일": st.column_config.TextColumn(
                "기준일", help="이 지표들을 계산한 가장 최근 거래일",
            ),
        },
    )
    csv_df = df_final.drop(columns=["_종목명_plain"], errors="ignore").copy()
    if "_종목명_plain" in df_final.columns:
        csv_df["종목명"] = df_final["_종목명_plain"]
    csv_bytes = csv_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV 다운로드",
        data=csv_bytes,
        file_name="Korea_Chart_Screened_Stocks.csv",
        mime="text/csv",
    )

with st.expander("ℹ️ 조건 설명"):
    st.markdown(
        """
- **추세 조건**: 월봉 종가가 5개월 이동평균선 위에서 연속으로 머문 개월 수(이번달 포함, 기본 3개월 이상)
- **급증 조건**: 최근 거래일 거래량·거래대금이 모두 직전 20거래일 평균 대비 배수 이상(기본 3배)
- **제외 필터 1**: 최근 3개월 저점 대비 현재가 상승률이 기준(기본 30%) 이상이면 제외
  (이미 크게 오른 뒤에 나온 거래량 증가는 단기 과열/막차 신호일 수 있어 배제)
- **제외 필터 2**: 급증 신호가 나온 날의 거래대금이 절대금액 기준(기본 1,500억원) 미만이면 제외
  (소형주에서 배수만 큰 노이즈성 급증을 배제 — 통과하려면 그날 거래대금 자체가 1,500억원 이상이어야 함)
        """
    )

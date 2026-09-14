import pandas as pd
import streamlit as st

from chart_screener import load_chart_screener_cache

st.title("📊 차트 분석 종목 찾기")
st.caption("1단계 장기 추세 필터 → 2단계 수급 유효성 검증 → 3단계 상투 분산 배제, 3단계 시스템 트레이딩 스크리닝")

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
    "거래량 급증·캔들 형태는 그날그날 새로 나오는 신호라 매일 자동 갱신됩니다. "
    "4단계(2차 파동 타점)는 시간 흐름 추적이 필요한 별도 로직이라 이번 화면에는 포함되어 있지 않습니다."
)

with st.sidebar:
    st.header("⚙️ 1단계 · 장기 추세 필터")
    min_consecutive_months = st.slider(
        "월봉 종가가 5개월선 위에서 연속으로 머문 개월 수 (이상)",
        min_value=1, max_value=12, value=3,
        help="월봉 종가가 5개월 이동평균선 위에 있었던 가장 최근부터의 연속 개월 수(이번달 포함) 기준",
    )

    st.header("⚙️ 2단계 · 수급 유효성 검증")
    min_volume_ratio_pct = st.number_input(
        "상대 거래량 배수 (%) 이상 (기준 300~500%)", min_value=100.0, value=300.0, step=50.0,
        help="최근 거래일 거래량 ÷ 직전 20거래일 평균 × 100. 500%는 참고용 상한이며, 초과해도 통과합니다(제외하지 않음).",
    )
    large_cap_threshold_eok = st.number_input(
        "대형주 기준 시가총액 (억원) 이상", min_value=0.0, value=10000.0, step=1000.0,
        help="이 값 이상이면 대형주, 미만이면 중소형주로 분류",
    )
    min_value_large_eok = st.number_input(
        "대형주 당일 거래대금 (억원) 이상", min_value=0.0, value=1000.0, step=100.0,
    )
    min_value_small_eok = st.number_input(
        "중소형주 당일 거래대금 (억원) 이상", min_value=0.0, value=500.0, step=50.0,
    )

    st.header("⚙️ 3단계 · 상투 분산 배제")
    max_rise_from_low_pct = st.number_input(
        "최근 6개월 저점 대비 상승률 (%) 초과 시 제외",
        min_value=0.0, value=80.0, step=5.0,
    )
    max_monthly_ma5_disparity_pct = st.number_input(
        "월봉 5MA 이격도 (%) 초과 시 제외", min_value=100.0, value=115.0, step=1.0,
        help="월봉 종가 ÷ 5개월 이동평균선 × 100",
    )
    max_daily_ma60_disparity_pct = st.number_input(
        "일봉 60MA 이격도 (%) 초과 시 제외", min_value=100.0, value=108.0, step=1.0,
        help="현재가 ÷ 일봉 60일 이동평균선 × 100",
    )
    max_upper_wick_body_pct = st.number_input(
        "대량거래일 윗꼬리/몸통 비율 (%) 초과 시 제외", min_value=0.0, value=100.0, step=10.0,
        help="대량거래일(=최근 거래일) 캔들의 (고가 - max(시가,종가)) ÷ |종가-시가| × 100. "
        "100% 초과면 윗꼬리가 몸통보다 길다는 뜻으로, 고점 매도 압력(세력 이탈) 신호로 봅니다.",
    )

    market = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True, key="chart_market")

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True, key="chart_run")

if run_clicked:
    df = pd.DataFrame(cache["rows"])
    if market != "전체":
        df = df[df["시장구분"] == market]

    required_cols = [
        "5개월선_연속상회월수", "일간_거래량배수", "시가총액", "당일_거래대금",
        "6개월저점대비_상승률(%)", "월봉5MA이격도(%)", "일봉60MA이격도(%)", "윗꼬리몸통비율(%)",
    ]
    df = df.dropna(subset=required_cols)

    large_cap_threshold = large_cap_threshold_eok * 100_000_000
    min_value_large = min_value_large_eok * 100_000_000
    min_value_small = min_value_small_eok * 100_000_000

    is_large = df["시가총액"] >= large_cap_threshold
    required_value_floor = is_large.map({True: min_value_large, False: min_value_small})

    cond = (
        (df["5개월선_연속상회월수"] >= min_consecutive_months)
        & (df["일간_거래량배수"] * 100 >= min_volume_ratio_pct)
        & (df["당일_거래대금"] >= required_value_floor)
        & (df["6개월저점대비_상승률(%)"] <= max_rise_from_low_pct)
        & (df["월봉5MA이격도(%)"] <= max_monthly_ma5_disparity_pct)
        & (df["일봉60MA이격도(%)"] <= max_daily_ma60_disparity_pct)
        & (df["윗꼬리몸통비율(%)"] <= max_upper_wick_body_pct)
    )
    df_screened = df[cond].copy()
    if not df_screened.empty:
        df_screened = df_screened.sort_values("일간_거래량배수", ascending=False)
        df_screened["시가총액(억)"] = (df_screened["시가총액"] / 100_000_000).round(0)
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
    st.caption("거래량 배수(일간) 높은 순으로 정렬되어 있습니다.")
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "종목코드", "종목명", "시장구분", "시총구분", "시가총액(억)", "현재가",
            "5개월선_연속상회월수", "일간_거래량배수", "당일_거래대금",
            "6개월저점대비_상승률(%)", "월봉5MA이격도(%)", "일봉60MA이격도(%)", "윗꼬리몸통비율(%)",
            "기준일",
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
            "시총구분": st.column_config.TextColumn(
                "시총구분", help="사이드바에서 설정한 시가총액 기준(기본 1조원)에 따른 대형주/중소형주 구분",
            ),
            "시가총액(억)": st.column_config.NumberColumn(
                "시가총액(억)", help="현재 시가총액 (단위: 억원)",
            ),
            "현재가": st.column_config.NumberColumn(
                "현재가", help="가장 최근 거래일 종가 (원)",
            ),
            "5개월선_연속상회월수": st.column_config.NumberColumn(
                "5개월선 연속상회(개월)",
                help="월봉 종가가 5개월 이동평균선 위에 있었던, 가장 최근 달(이번달 포함)부터의 연속 개월 수",
            ),
            "일간_거래량배수": st.column_config.NumberColumn(
                "거래량 배수(일간)", format="%.1f배",
                help="최근 거래일 거래량 ÷ 직전 20거래일 평균 거래량. 3~5배가 기준 구간(참고용, 5배 초과도 통과)",
            ),
            "당일_거래대금": st.column_config.NumberColumn(
                "당일 거래대금(원)", help="가장 최근 거래일의 거래대금 (종가 × 거래량으로 근사)",
            ),
            "6개월저점대비_상승률(%)": st.column_config.NumberColumn(
                "6개월저점 대비(%)", format="%.1f%%",
                help="최근 6개월 저점 대비 현재가 상승률. 너무 높으면 이미 크게 오른 뒤의 급증(상투)으로 보고 제외 대상",
            ),
            "월봉5MA이격도(%)": st.column_config.NumberColumn(
                "월봉5MA 이격도(%)", format="%.1f%%",
                help="월봉 종가 ÷ 5개월 이동평균선 × 100. 100%보다 크게 벌어질수록 평균회귀 급락 위험",
            ),
            "일봉60MA이격도(%)": st.column_config.NumberColumn(
                "일봉60MA 이격도(%)", format="%.1f%%",
                help="현재가 ÷ 일봉 60일 이동평균선 × 100. 100%보다 크게 벌어질수록 평균회귀 급락 위험",
            ),
            "윗꼬리몸통비율(%)": st.column_config.NumberColumn(
                "윗꼬리/몸통(%)", format="%.0f%%",
                help="대량거래일(최근 거래일) 캔들의 윗꼬리 ÷ 몸통 × 100. 100% 초과면 고점에서 매도 압력에 밀린 캔들(세력 이탈 신호)",
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

with st.expander("ℹ️ 조건 설명 (1~3단계)"):
    st.markdown(
        """
- **1단계 · 장기 추세 필터**: 월봉 종가가 5개월 이동평균선 위에서 연속으로 머문 개월 수(이번달 포함, 기본 3개월 이상)
  — 하락 추세 역배열 상태에서의 휩소(가짜 신호)를 걸러냅니다.
- **2단계 · 수급 유효성 검증**:
  - 상대 거래량 배수: 최근 거래일 거래량이 직전 20거래일 평균 대비 300~500% (기관·외국인 등 스마트머니 유입 확인 —
    500%를 넘어도 제외하지 않고 참고용으로만 표시합니다)
  - 거래대금 최소 기준(시가총액 규모별 차등): 대형주(기본 1조원 이상)는 당일 거래대금 1,000억원 이상,
    중소형주는 500억원 이상 — 대형주는 배수만으로는 잘 안 걸리고 중소형주는 배수는 쉽게 튀지만 절대금액이
    작으면 의미 있는 자금 유입인지 알기 어려워 규모별로 나눴습니다.
- **3단계 · 상투 분산 배제** (평균회귀 급락·세력 매도 리스크 회피):
  - 최근 6개월 저점 대비 상승률이 기준(기본 80%) 초과면 제외
  - 월봉 5MA 이격도가 기준(기본 115%) 초과면 제외
  - 일봉 60MA 이격도가 기준(기본 108%) 초과면 제외
  - 대량거래일(최근 거래일) 캔들의 윗꼬리가 몸통 대비 기준(기본 100%) 초과면 제외
    (윗꼬리가 길다 = 장중 고가까지 올랐다가 매도 물량에 밀려 내려온 캔들 — 세력이 물량을 넘기고 빠지는 신호로 해석)
        """
    )

with st.expander("📖 4~5단계 (이번 화면에는 미포함 — 참고용 설명)"):
    st.markdown(
        """
**4단계 · 2차 파동 타점** (미구현 — 별도 설계 필요)

1~3단계가 "오늘 하루의 스냅샷"만으로 판정 가능한 반면, 4단계는 1차 파동(돌파) 발생 이후
눌림목(조정)을 거쳐 재돌파하는 흐름을 시간에 걸쳐 추적해야 하는 로직이라 이번 구현 범위에서는
제외했습니다. 필요하시면 아래 항목들의 정확한 판정 규칙(스윙 고점/저점을 어떻게 정의할지,
1차 파동을 며칠~몇 달의 창으로 볼지 등)을 논의해 별도로 설계·구현할 수 있습니다.
- 1차 반등 후 되돌림 폭이 38.2%~50.0% 구간에서 지지
- 눌림목 구간의 일평균 거래량이 1차 대량거래량의 30% 이하로 마름
- 1차 파동 전고점을 종가로 돌파하며 거래량 250% 증가 시 매수

**5단계 · 청산 프로토콜** (코드 로직 없이 참고용 설명만 제공)

이 화면은 신규 진입 후보를 찾는 스크리너이고, 실제로 어떤 종목을 얼마에 보유 중인지는 알 수
없어 아래 규칙은 자동 판정하지 않습니다. 직접 보유 종목에 적용해보세요.
- 손절매: 진입 가격 대비 -6.5% 터치 시 장중 즉시 전량 매도
- 분할 익절: 진입 가격 대비 +25.0% 도달 시 보유 수량 50% 매도
- 추세 청산: 월봉 종가가 5개월 이동평균선을 하향 이탈 시 잔여 수량 전량 매도
        """
    )

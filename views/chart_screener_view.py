import pandas as pd
import streamlit as st

from chart_screener import load_chart_screener_cache

st.title("📊 차트 분석 종목 찾기")
st.caption("1단계 장기 추세 → 2단계 수급 유효성(최근 N봉 내) → 3단계 상투 분산 배제 → 4단계 재반등 확인, 4단계 시스템 트레이딩 스크리닝")

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
    "거래량 급증·재반등 확인은 그날그날 새로 나오는 신호라 매일 자동 갱신됩니다."
)


def _check_stage2(row, lookback_bars, vol_ratio_threshold_pct, value_floor):
    dates = row.get("최근30봉_일자") or []
    ratios = row.get("최근30봉_거래량배수_전일대비") or []
    values = row.get("최근30봉_거래대금") or []
    n = min(lookback_bars, len(dates))
    best = None
    for d, r, v in zip(dates[-n:], ratios[-n:], values[-n:]):
        if r is None or v is None:
            continue
        if r * 100 >= vol_ratio_threshold_pct and v >= value_floor:
            if best is None or d > best[0]:
                best = (d, r, v)
    if best:
        return pd.Series({"2단계_충족": True, "2단계_충족일": best[0], "2단계_충족일_거래량배수": best[1], "2단계_충족일_거래대금": best[2]})
    return pd.Series({"2단계_충족": False, "2단계_충족일": None, "2단계_충족일_거래량배수": None, "2단계_충족일_거래대금": None})


with st.sidebar:
    st.header("⚙️ 1단계 · 장기 추세 필터")
    min_consecutive_months = st.slider(
        "월봉 종가가 5개월선 위에서 연속으로 머문 개월 수 (이상)",
        min_value=1, max_value=12, value=3,
        help="월봉 종가가 5개월 이동평균선 위에 있었던 가장 최근부터의 연속 개월 수(이번달 포함) 기준",
    )

    st.header("⚙️ 2단계 · 수급 유효성 검증")
    lookback_bars = st.slider(
        "최근 며칠(봉) 이내에 발생했는지 확인 (최대 30봉)",
        min_value=5, max_value=30, value=20,
        help="이 기간 안에 거래량 배수·거래대금 조건을 동시에 만족한 날이 하루라도 있으면 통과",
    )
    min_volume_ratio_pct = st.number_input(
        "거래량 배수 (%) 이상 (전일 대비)", min_value=100.0, value=300.0, step=50.0,
        help="그날 거래량 ÷ 전일 거래량 × 100",
    )
    large_cap_threshold_eok = st.number_input(
        "대형주 기준 시가총액 (억원) 이상", min_value=0.0, value=10000.0, step=1000.0,
        help="이 값 이상이면 대형주, 미만이면 소형주로 분류",
    )
    min_value_large_eok = st.number_input(
        "대형주 거래대금 (억원) 이상", min_value=0.0, value=1000.0, step=100.0,
    )
    min_value_small_eok = st.number_input(
        "소형주 거래대금 (억원) 이상", min_value=0.0, value=500.0, step=50.0,
    )

    st.header("⚙️ 3단계 · 상투 분산 배제")
    max_rise_from_low_pct = st.number_input(
        "120거래일(약 6개월) 저점 대비 상승률 (%) 초과 시 제외",
        min_value=0.0, value=80.0, step=5.0,
    )
    max_monthly_ma5_disparity_pct = st.number_input(
        "월봉 5MA 이격도 (%) 초과 시 제외", min_value=100.0, value=115.0, step=1.0,
        help="월봉 종가 ÷ 5개월 이동평균선 × 100",
    )
    max_daily_ma60_disparity_pct = st.number_input(
        "일봉 60MA 이격도 (%) 초과 시 제외", min_value=100.0, value=112.0, step=1.0,
        help="현재가 ÷ 일봉 60일 이동평균선 × 100",
    )

    st.header("⚙️ 4단계 · 재반등 확인")
    require_stage4 = st.checkbox(
        "당일 5일선 이상 또는 당일 양봉 조건 적용", value=True,
        help="눌림목 이후 재반등 신호 — 당일 종가가 일봉 5일 이동평균선 이상이거나, 당일 종가가 시가보다 높으면(양봉) 통과",
    )

    market = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True, key="chart_market")

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True, key="chart_run")

if run_clicked:
    df = pd.DataFrame(cache["rows"])
    if market != "전체":
        df = df[df["시장구분"] == market]

    required_cols = [
        "5개월선_연속상회월수", "시가총액", "120봉저점대비_상승률(%)",
        "월봉5MA이격도(%)", "일봉60MA이격도(%)", "당일5일선이상", "당일양봉",
    ]
    df = df.dropna(subset=required_cols)

    large_cap_threshold = large_cap_threshold_eok * 100_000_000
    min_value_large = min_value_large_eok * 100_000_000
    min_value_small = min_value_small_eok * 100_000_000

    is_large = df["시가총액"] >= large_cap_threshold
    df["_value_floor"] = is_large.map({True: min_value_large, False: min_value_small})

    stage2 = df.apply(lambda r: _check_stage2(r, lookback_bars, min_volume_ratio_pct, r["_value_floor"]), axis=1)
    df = pd.concat([df, stage2], axis=1)

    cond = (
        (df["5개월선_연속상회월수"] >= min_consecutive_months)
        & (df["2단계_충족"])
        & (df["120봉저점대비_상승률(%)"] <= max_rise_from_low_pct)
        & (df["월봉5MA이격도(%)"] <= max_monthly_ma5_disparity_pct)
        & (df["일봉60MA이격도(%)"] <= max_daily_ma60_disparity_pct)
    )
    if require_stage4:
        cond = cond & (df["당일5일선이상"] | df["당일양봉"])

    df_screened = df[cond].copy()
    if not df_screened.empty:
        df_screened = df_screened.sort_values("2단계_충족일_거래량배수", ascending=False)
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
    st.caption("2단계 충족일 거래량 배수 높은 순으로 정렬되어 있습니다.")
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "종목코드", "종목명", "시장구분", "시총구분", "시가총액(억)", "현재가",
            "5개월선_연속상회월수",
            "2단계_충족일", "2단계_충족일_거래량배수", "2단계_충족일_거래대금",
            "120봉저점대비_상승률(%)", "월봉5MA이격도(%)", "일봉60MA이격도(%)",
            "당일5일선이상", "당일양봉", "기준일",
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
                "시총구분", help="사이드바에서 설정한 시가총액 기준(기본 1조원)에 따른 대형주/소형주 구분",
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
            "2단계_충족일": st.column_config.TextColumn(
                "수급충족일", help="최근 조회 구간 내에서 거래량·거래대금 조건을 동시에 만족한 가장 최근 날짜",
            ),
            "2단계_충족일_거래량배수": st.column_config.NumberColumn(
                "충족일 거래량배수", format="%.1f배", help="충족일의 거래량 ÷ 전일 거래량",
            ),
            "2단계_충족일_거래대금": st.column_config.NumberColumn(
                "충족일 거래대금(원)", help="충족일의 거래대금 (종가 × 거래량으로 근사)",
            ),
            "120봉저점대비_상승률(%)": st.column_config.NumberColumn(
                "120봉저점 대비(%)", format="%.1f%%",
                help="최근 120거래일(약 6개월) 저점 대비 현재가 상승률. 너무 높으면 이미 크게 오른 뒤(상투)로 보고 제외 대상",
            ),
            "월봉5MA이격도(%)": st.column_config.NumberColumn(
                "월봉5MA 이격도(%)", format="%.1f%%",
                help="월봉 종가 ÷ 5개월 이동평균선 × 100. 100%보다 크게 벌어질수록 평균회귀 급락 위험",
            ),
            "일봉60MA이격도(%)": st.column_config.NumberColumn(
                "일봉60MA 이격도(%)", format="%.1f%%",
                help="현재가 ÷ 일봉 60일 이동평균선 × 100. 100%보다 크게 벌어질수록 평균회귀 급락 위험",
            ),
            "당일5일선이상": st.column_config.CheckboxColumn(
                "당일≥5일선", help="당일 종가가 일봉 5일 이동평균선 이상인지",
            ),
            "당일양봉": st.column_config.CheckboxColumn(
                "당일양봉", help="당일 종가가 시가보다 높은지(양봉)",
            ),
            "기준일": st.column_config.TextColumn(
                "기준일", help="이 지표들을 계산한 가장 최근 거래일",
            ),
        },
    )
    csv_df = df_final.drop(columns=["_종목명_plain", "_value_floor"], errors="ignore").copy()
    if "_종목명_plain" in df_final.columns:
        csv_df["종목명"] = df_final["_종목명_plain"]
    csv_bytes = csv_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV 다운로드",
        data=csv_bytes,
        file_name="Korea_Chart_Screened_Stocks.csv",
        mime="text/csv",
    )

with st.expander("ℹ️ 조건 설명 (1~4단계)"):
    st.markdown(
        """
- **1단계 · 장기 추세 필터**: 월봉 종가가 5개월 이동평균선 위에서 연속으로 머문 개월 수(이번달 포함, 기본 3개월 이상)
- **2단계 · 수급 유효성 검증**: 최근 N봉(기본 20봉) 이내에 "그날 거래량이 전일 대비 300% 이상"이면서
  "그날 거래대금이 시총 규모별 기준(대형주 1,000억/소형주 500억) 이상"인 날이 동시에 1회 이상 있었는지 확인
  (당일이 아니어도, 최근 며칠 내 그런 수급 이벤트가 있었으면 통과 — 이벤트 발생일과 당일 시점의 다른 조건이
  같은 날일 필요는 없습니다)
- **3단계 · 상투 분산 배제**: 120거래일(약 6개월) 저점 대비 상승률(기본 80% 이하), 월봉 5MA 이격도(기본 115% 이하),
  일봉 60MA 이격도(기본 112% 이하) — 평균회귀 급락 리스크 회피
- **4단계 · 재반등 확인**: 당일 종가가 일봉 5일 이동평균선 이상이거나, 당일 종가가 시가보다 높으면(양봉) 통과
  — 눌림목 이후 재반등하는 흐름인지 확인
        """
    )

with st.expander("📖 청산 프로토콜 (참고용 — 자동 판정하지 않음)"):
    st.markdown(
        """
이 화면은 신규 진입 후보를 찾는 스크리너이고, 실제로 어떤 종목을 얼마에 보유 중인지는 알 수
없어 아래 규칙은 자동 판정하지 않습니다. 직접 보유 종목에 적용해보세요.
- 손절매: 진입 가격 대비 -6.5% 터치 시 장중 즉시 전량 매도
- 분할 익절: 진입 가격 대비 +25.0% 도달 시 보유 수량 50% 매도
- 추세 청산: 월봉 종가가 5개월 이동평균선을 하향 이탈 시 잔여 수량 전량 매도
        """
    )

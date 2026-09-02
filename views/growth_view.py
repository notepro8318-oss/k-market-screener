import pandas as pd
import streamlit as st

from screener import (
    GROWTH_DEFAULT_FILTER_CRITERIA,
    load_cache_meta,
    run_growth_pipeline_from_cache,
)

st.title("🚀 성장주 찾기")
st.caption("탑라인 성장성 · 수익 레버리지 · 밸류에이션(PEG) · 자본 효율성 · 재무 안정성 기반 스크리닝")

cache_meta = load_cache_meta()
if cache_meta is None:
    st.error(
        "캐시 데이터가 아직 없습니다. OpenDART가 이 서버(해외 IP)에서의 연결을 차단해 "
        "실시간 조회가 불가능하므로, 로컬 환경에서 `python batch.py`를 실행해 "
        "`data/screening_cache.csv`를 만든 뒤 커밋/푸시해야 합니다."
    )
    st.stop()

st.caption(
    f"데이터 기준일: {cache_meta['generated_at']} (종목 {cache_meta['count']}개) — "
    "재무 데이터는 그 시점 기준 최근 12개월(TTM)·최근 분기·최근 3개 사업연도 실적입니다."
)
st.info(
    "Forward PEG는 애널리스트 컨센서스가 없어 대신 **Trailing PEG**(PER ÷ 영업이익 3개년 CAGR)로 "
    "근사합니다. R&D 비율 지표는 DART 공시 서식 조사 후 추가 예정입니다.",
    icon="ℹ️",
)

with st.sidebar:
    st.header("⚙️ 스크리닝 조건")

    market = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True, key="growth_market")

    st.caption("탑라인 성장성")
    min_revenue_cagr = st.number_input(
        "매출액 3개년 CAGR (%) 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_REVENUE_CAGR_3Y"], step=1.0,
    )
    min_revenue_yoy_q = st.number_input(
        "최근 분기 매출액 YoY (%) 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_REVENUE_YOY_Q"], step=1.0,
    )

    st.caption("수익 레버리지")
    min_op_cagr = st.number_input(
        "영업이익 3개년 CAGR (%) 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_OP_INCOME_CAGR_3Y"], step=1.0,
    )
    min_op_yoy_q = st.number_input(
        "최근 분기 영업이익 YoY (%) 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_OP_INCOME_YOY_Q"], step=1.0,
    )

    st.caption("가치 평가 / 자본 효율성")
    max_peg = st.number_input(
        "Trailing PEG 이하", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MAX_PEG"], step=0.1,
    )
    min_roe = st.number_input(
        "ROE (%) 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_ROE"], step=1.0,
    )
    min_roic = st.number_input(
        "ROIC (%) 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_ROIC"], step=1.0,
    )

    st.caption("재무 안정성")
    max_debt_ratio = st.number_input(
        "부채비율 (%) 이하", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MAX_DEBT_RATIO"], step=10.0,
    )
    min_interest_coverage = st.number_input(
        "이자보상배율 이상", min_value=0.0,
        value=GROWTH_DEFAULT_FILTER_CRITERIA["MIN_INTEREST_COVERAGE"], step=0.5,
    )

    criteria = {
        "MARKET": market,
        "MIN_REVENUE_CAGR_3Y": min_revenue_cagr,
        "MIN_REVENUE_YOY_Q": min_revenue_yoy_q,
        "MIN_OP_INCOME_CAGR_3Y": min_op_cagr,
        "MIN_OP_INCOME_YOY_Q": min_op_yoy_q,
        "MAX_PEG": max_peg,
        "MIN_ROE": min_roe,
        "MIN_ROIC": min_roic,
        "MAX_DEBT_RATIO": max_debt_ratio,
        "MIN_INTEREST_COVERAGE": min_interest_coverage,
    }

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True, key="growth_run")

if run_clicked:
    df_screened = run_growth_pipeline_from_cache(criteria)
    if not df_screened.empty:
        df_screened = df_screened.copy()
        df_screened["_종목명_plain"] = df_screened["종목명"]
        df_screened["종목명"] = df_screened.apply(
            lambda r: f"https://finance.naver.com/item/main.naver?code={r['종목코드']}#{r['_종목명_plain']}",
            axis=1,
        )
    # st.button()은 클릭된 그 rerun에서만 True이므로, 이후 다른 위젯 조작으로 재실행돼도
    # 결과가 사라지지 않도록 session_state에 저장해두고 그걸 읽어서 그린다.
    st.session_state["growth_screening_df"] = df_screened

df_final = st.session_state.get("growth_screening_df")

if df_final is None:
    st.info("왼쪽에서 조건을 설정한 뒤 **스크리닝 실행** 버튼을 눌러주세요.")
elif df_final.empty:
    st.warning(
        "조건을 모두 만족하는 종목이 없습니다. 조건을 완화한 뒤 다시 시도해보세요. "
        "(최근 분기 YoY는 사업보고서 시점엔 계산되지 않아 조건에 포함하면 그 시점에는 종목이 안 나올 수 있습니다.)"
    )
else:
    st.success(f"{len(df_final)}개 종목이 조건을 통과했습니다.")
    st.caption("PEG가 낮은(성장 대비 저평가) 순으로 정렬되어 있습니다.")
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "종목코드", "종목명", "시장구분", "시가총액(억)", "PER", "PEG",
            "매출액_3개년CAGR(%)", "매출액_최근분기YoY(%)",
            "영업이익_3개년CAGR(%)", "영업이익_최근분기YoY(%)",
            "ROE(%)", "ROIC(%)", "부채비율(%)", "이자보상배율", "F-Score", "기준보고서",
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
            "시가총액(억)": st.column_config.NumberColumn(
                "시가총액(억)", help="발행주식수 × 현재가로 계산한 시가총액 (단위: 억원)",
            ),
            "PER": st.column_config.NumberColumn(
                "PER", help="주가수익비율 = 시가총액 ÷ TTM(최근 12개월) 순이익",
            ),
            "PEG": st.column_config.NumberColumn(
                "PEG",
                help="Trailing PEG = PER ÷ 영업이익 3개년 CAGR(%). Forward 컨센서스 대신 과거 성장률로 근사 "
                "— 1 이하면 성장률 대비 저평가, 0.5 이하면 적극적 저평가로 해석",
            ),
            "매출액_3개년CAGR(%)": st.column_config.NumberColumn(
                "매출CAGR(3y,%)", help="최근 3개 사업연도 매출액 연평균성장률 — 전방 수요 확장 여부",
            ),
            "매출액_최근분기YoY(%)": st.column_config.NumberColumn(
                "매출YoY(분기,%)", help="가장 최근 단일 분기 매출액의 전년 동기 대비 증가율",
            ),
            "영업이익_3개년CAGR(%)": st.column_config.NumberColumn(
                "영업이익CAGR(3y,%)", help="최근 3개 사업연도 영업이익 연평균성장률 — 영업레버리지 발현 여부",
            ),
            "영업이익_최근분기YoY(%)": st.column_config.NumberColumn(
                "영업이익YoY(분기,%)", help="가장 최근 단일 분기 영업이익의 전년 동기 대비 증가율",
            ),
            "ROE(%)": st.column_config.NumberColumn(
                "ROE(%)", help="자기자본이익률 = 순이익 ÷ 자기자본 × 100 (TTM 기준)",
            ),
            "ROIC(%)": st.column_config.NumberColumn(
                "ROIC(%)",
                help="투하자본이익률 = 세후영업이익(NOPAT) ÷ (이자부부채+자기자본-현금성자산) × 100 (TTM 기준). "
                "부채로 만든 착시를 배제한 순수 영업 자본수익률",
            ),
            "부채비율(%)": st.column_config.NumberColumn(
                "부채비율(%)", help="부채총계 ÷ 자기자본 × 100 (가장 최근 확정 사업연도 기준)",
            ),
            "이자보상배율": st.column_config.NumberColumn(
                "이자보상배율", help="영업이익 ÷ 이자비용 (가장 최근 확정 사업연도 기준). 높을수록 이자 지급 여력이 큼",
            ),
            "F-Score": st.column_config.NumberColumn(
                "F-Score", help="Piotroski F-Score (0~9점). 참고용 — 성장주 필터링 기준에는 포함되지 않음",
            ),
            "기준보고서": st.column_config.TextColumn(
                "기준보고서", help="TTM/최근 분기 계산에 사용된 최신 DART 공시(사업/반기/분기보고서)의 접수일",
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
        file_name="Korea_Growth_Stocks.csv",
        mime="text/csv",
    )

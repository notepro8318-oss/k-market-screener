import pandas as pd
import streamlit as st

from screener import (
    DEFAULT_FILTER_CRITERIA,
    compute_priority_scores,
    load_cache_meta,
    run_pipeline_from_cache,
)

st.title("📈 수익가치주 찾기")
st.caption("시가총액 · PER · PBR · 수익성(OPM/ROA/ROE) · Piotroski F-Score 기반 2단계 필터링")

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
    "재무 데이터는 그 시점 기준 최근 12개월(TTM) 실적입니다. "
    "OpenDART가 해외 IP를 차단해 실시간 조회 대신 로컬에서 주기적으로 갱신한 캐시를 사용합니다."
)


with st.sidebar:
    st.header("⚙️ 스크리닝 조건")

    market = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True)

    min_marcap_eok = st.number_input(
        "최소 시가총액 (억원)", min_value=0,
        value=DEFAULT_FILTER_CRITERIA["MIN_MARCAP"] // 100_000_000, step=100,
    )
    max_per = st.number_input(
        "최대 PER", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MAX_PER"], step=0.5,
    )
    max_pbr = st.number_input(
        "최대 PBR", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MAX_PBR"], step=0.1,
    )
    min_opm = st.number_input(
        "최소 영업이익률 (%)", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MIN_OPM"], step=0.5,
    )
    min_roa = st.number_input(
        "최소 ROA (%)", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MIN_ROA"], step=0.5,
    )
    min_roe = st.number_input(
        "최소 ROE (%)", min_value=0.0, value=DEFAULT_FILTER_CRITERIA["MIN_ROE"], step=0.5,
    )
    min_trading_val_eok = st.number_input(
        "최소 20일 평균 거래대금 (억원)", min_value=0.0,
        value=DEFAULT_FILTER_CRITERIA["MIN_TRADING_VALUE_20D"] / 100_000_000, step=0.5,
    )
    min_fscore = st.slider(
        "최소 F-Score", min_value=0, max_value=9, value=DEFAULT_FILTER_CRITERIA["MIN_FSCORE"],
    )

    with st.expander("🏆 투자 우선순위 가중치", expanded=False):
        st.caption("스크리닝 결과 안에서만 상대 순위를 매겨 종합점수(0~100점)를 계산합니다.")
        w_value = st.slider("저평가 (PER·PBR)", 0, 100, 33)
        w_quality = st.slider("수익성 (영업이익률·ROA·ROE·F-Score)", 0, 100, 33)
        w_trend = st.slider("개선추세 (OPM/ROA/ROE 추세)", 0, 100, 34)

    weights = {"value": w_value, "quality": w_quality, "trend": w_trend}

    criteria = {
        "MARKET": market,
        "MIN_MARCAP": min_marcap_eok * 100_000_000,
        "MAX_PER": max_per,
        "MAX_PBR": max_pbr,
        "MIN_OPM": min_opm,
        "MIN_ROA": min_roa,
        "MIN_ROE": min_roe,
        "MIN_TRADING_VALUE_20D": min_trading_val_eok * 100_000_000,
        "MIN_FSCORE": min_fscore,
    }

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True)

if run_clicked:
    df_screened = run_pipeline_from_cache(criteria)
    if not df_screened.empty:
        df_screened = df_screened.copy()
        df_screened["종합점수"] = compute_priority_scores(df_screened, weights)
        df_screened = df_screened.sort_values(by="종합점수", ascending=False)
        df_screened.insert(0, "우선순위", range(1, len(df_screened) + 1))

        df_screened["_종목명_plain"] = df_screened["종목명"]
        df_screened["종목명"] = df_screened.apply(
            lambda r: f"https://finance.naver.com/item/main.naver?code={r['종목코드']}#{r['_종목명_plain']}",
            axis=1,
        )
    # 스크리닝 실행 버튼을 누른 그 rerun에서만 run_clicked가 True이므로, 아래에서 다른 위젯(종목
    # 선택 등)을 조작해도 결과가 초기화되지 않도록 session_state에 저장해두고 그걸 읽어서 그린다.
    st.session_state["screening_df"] = df_screened

df_final = st.session_state.get("screening_df")

if df_final is None:
    st.info("왼쪽에서 조건을 설정한 뒤 **스크리닝 실행** 버튼을 눌러주세요.")
elif df_final.empty:
    st.warning("조건을 모두 만족하는 종목이 없습니다. 조건을 완화한 뒤 다시 시도해보세요.")
else:
    st.success(f"{len(df_final)}개 종목이 조건을 통과했습니다.")
    st.caption(
        "추세 컬럼은 과거 최대 3개 사업연도 + 현재 TTM(가장 오른쪽) 흐름입니다. "
        "종합점수는 이 스크리닝 결과 안에서의 상대 순위를 합산한 투자 우선순위(0~100점, 높을수록 우선)입니다."
    )
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "우선순위", "종합점수", "종목코드", "종목명", "시장구분", "시가총액(억)", "20일거래대금(억)",
            "PER", "PER_trend", "PBR", "PBR_trend",
            "영업이익률(%)", "OPM_trend", "ROA(%)", "ROA_trend", "ROE(%)", "ROE_trend",
            "F-Score", "기준보고서",
        ],
        column_config={
            "우선순위": st.column_config.NumberColumn(
                "우선순위", help="종합점수 기준 이 스크리닝 결과 내 순위 (1위가 최우선)",
            ),
            "종합점수": st.column_config.NumberColumn(
                "종합점수",
                help="저평가(PER·PBR)·수익성(영업이익률·ROA·ROE·F-Score)·개선추세(OPM/ROA/ROE 추세)"
                " 순위를 사이드바 가중치로 합산한 점수 (0~100점, 이 스크리닝 결과 내 상대 순위 기준)",
            ),
            "종목코드": st.column_config.TextColumn(
                "종목코드", help="한국거래소(KRX) 상장 종목 코드 (6자리)",
            ),
            "종목명": st.column_config.LinkColumn(
                "종목명",
                help="클릭하면 네이버증권 해당 종목 페이지로 이동합니다",
                display_text=r"#(.*)$",
            ),
            "시장구분": st.column_config.TextColumn(
                "시장구분", help="상장 시장 (KOSPI 또는 KOSDAQ)",
            ),
            "시가총액(억)": st.column_config.NumberColumn(
                "시가총액(억)", help="발행주식수 × 현재가로 계산한 시가총액 (단위: 억원)",
            ),
            "20일거래대금(억)": st.column_config.NumberColumn(
                "20일거래대금(억)", help="최근 20거래일 평균 거래대금 (단위: 억원). 유동성이 낮은 종목을 걸러내는 데 사용",
            ),
            "PER": st.column_config.NumberColumn(
                "PER", help="주가수익비율 = 시가총액 ÷ TTM(최근 12개월) 순이익. 낮을수록 이익 대비 저평가",
            ),
            "PER_trend": st.column_config.LineChartColumn(
                "PER 추세", width="small",
                help="과거 최대 3개 사업연도 + 현재 TTM(가장 오른쪽) PER 흐름",
            ),
            "PBR": st.column_config.NumberColumn(
                "PBR", help="주가순자산비율 = 시가총액 ÷ 자기자본(순자산). 낮을수록 자산 대비 저평가",
            ),
            "PBR_trend": st.column_config.LineChartColumn(
                "PBR 추세", width="small",
                help="과거 최대 3개 사업연도 + 현재 TTM(가장 오른쪽) PBR 흐름",
            ),
            "영업이익률(%)": st.column_config.NumberColumn(
                "영업이익률(%)", help="영업이익 ÷ 매출액 × 100 (TTM 기준). 본업의 수익성 지표",
            ),
            "OPM_trend": st.column_config.LineChartColumn(
                "영업이익률 추세", width="small",
                help="과거 최대 3개 사업연도 + 현재 TTM(가장 오른쪽) 영업이익률 흐름",
            ),
            "ROA(%)": st.column_config.NumberColumn(
                "ROA(%)", help="총자산이익률 = 순이익 ÷ 총자산 × 100 (TTM 기준). 자산을 얼마나 효율적으로 굴렸는지 지표",
            ),
            "ROA_trend": st.column_config.LineChartColumn(
                "ROA 추세", width="small",
                help="과거 최대 3개 사업연도 + 현재 TTM(가장 오른쪽) ROA 흐름",
            ),
            "ROE(%)": st.column_config.NumberColumn(
                "ROE(%)", help="자기자본이익률 = 순이익 ÷ 자기자본 × 100 (TTM 기준). 주주 자본 대비 수익성 지표",
            ),
            "ROE_trend": st.column_config.LineChartColumn(
                "ROE 추세", width="small",
                help="과거 최대 3개 사업연도 + 현재 TTM(가장 오른쪽) ROE 흐름",
            ),
            "F-Score": st.column_config.NumberColumn(
                "F-Score",
                help="Piotroski F-Score (0~9점). 수익성·재무건전성·효율성 9개 항목 중 충족한 개수 — 높을수록 우량",
            ),
            "기준보고서": st.column_config.TextColumn(
                "기준보고서", help="TTM 계산에 사용된 최신 DART 공시(사업/반기/분기보고서)의 접수일",
            ),
        },
    )
    financials_5y_cols = [
        "연도_5y", "매출액_5y", "경상이익_5y", "경상이익률_5y",
        "CFO_5y", "CFI_5y", "CFF_5y", "FCF_5y",
    ]
    csv_df = df_final.drop(
        columns=["PER_trend", "PBR_trend", "OPM_trend", "ROA_trend", "ROE_trend"] + financials_5y_cols,
        errors="ignore",
    ).copy()
    csv_df["종목명"] = csv_df["_종목명_plain"]
    csv_bytes = csv_df.drop(columns=["_종목명_plain"]).to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV 다운로드",
        data=csv_bytes,
        file_name="Korea_Value_HighQuality_Stocks.csv",
        mime="text/csv",
    )

    st.subheader("📊 5년 재무 상세")
    if "연도_5y" not in df_final.columns:
        st.info("캐시가 아직 5년 재무 데이터를 포함하기 전 버전입니다. 캐시가 갱신되면 표시됩니다.")
    else:
        select_labels = (df_final["_종목명_plain"] + " (" + df_final["종목코드"] + ")").tolist()
        picked_label = st.selectbox("종목 선택", select_labels, key="detail_stock_picker")
        picked_row = df_final.iloc[select_labels.index(picked_label)]

        years = picked_row["연도_5y"]
        if not years:
            st.info(
                "이 종목은 5년치 재무 데이터가 없습니다 "
                "(상장 5년 미만이거나, 은행·금융지주 등 계정 구조가 크게 다른 업종일 수 있습니다)."
            )
        else:
            detail_df = pd.DataFrame({
                "연도": [str(y) for y in years],
                "매출액(억)": [round(v / 100_000_000, 1) for v in picked_row["매출액_5y"]],
                "경상이익(억)": [round(v / 100_000_000, 1) for v in picked_row["경상이익_5y"]],
                "경상이익률(%)": picked_row["경상이익률_5y"],
                "영업활동CF(억)": [round(v / 100_000_000, 1) for v in picked_row["CFO_5y"]],
                "투자활동CF(억)": [round(v / 100_000_000, 1) for v in picked_row["CFI_5y"]],
                "재무활동CF(억)": [round(v / 100_000_000, 1) for v in picked_row["CFF_5y"]],
                "잉여현금흐름(억)": [round(v / 100_000_000, 1) for v in picked_row["FCF_5y"]],
            })
            st.dataframe(
                detail_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "연도": st.column_config.TextColumn(
                        "연도", help="사업보고서 기준 결산연도",
                    ),
                    "매출액(억)": st.column_config.NumberColumn(
                        "매출액(억)", help="해당 사업연도 매출액 (단위: 억원)",
                    ),
                    "경상이익(억)": st.column_config.NumberColumn(
                        "경상이익(억)",
                        help="현행 K-IFRS엔 '경상이익' 계정이 없어 세전이익(법인세비용차감전순이익)으로 대체 (단위: 억원)",
                    ),
                    "경상이익률(%)": st.column_config.NumberColumn(
                        "경상이익률(%)", help="경상이익(세전이익) ÷ 매출액 × 100",
                    ),
                    "영업활동CF(억)": st.column_config.NumberColumn(
                        "영업활동CF(억)", help="영업활동현금흐름 (단위: 억원). 본업에서 실제로 들어오고 나간 현금",
                    ),
                    "투자활동CF(억)": st.column_config.NumberColumn(
                        "투자활동CF(억)", help="투자활동현금흐름 (단위: 억원). 설비투자·자산 취득/처분 등에 따른 현금 흐름",
                    ),
                    "재무활동CF(억)": st.column_config.NumberColumn(
                        "재무활동CF(억)", help="재무활동현금흐름 (단위: 억원). 차입·상환·배당·증자 등에 따른 현금 흐름",
                    ),
                    "잉여현금흐름(억)": st.column_config.NumberColumn(
                        "잉여현금흐름(억)", help="FCF ≈ 영업활동현금흐름 + 투자활동현금흐름 (단위: 억원)",
                    ),
                },
            )
            st.line_chart(detail_df.set_index("연도")[["매출액(억)", "경상이익(억)"]])
            st.line_chart(
                detail_df.set_index("연도")[
                    ["영업활동CF(억)", "투자활동CF(억)", "재무활동CF(억)", "잉여현금흐름(억)"]
                ]
            )
            st.caption(
                "경상이익은 세전이익(법인세비용차감전순이익)으로 대체한 값이며, "
                "잉여현금흐름(FCF)은 영업활동현금흐름 + 투자활동현금흐름으로 근사한 값입니다."
            )

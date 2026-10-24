import math

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
    "근사합니다.",
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

    st.caption("기술 선도력")
    apply_rnd_filter = st.checkbox(
        "R&D 비율 조건 적용 (제조업 5% / 첨단IT 10% 이상)",
        value=GROWTH_DEFAULT_FILTER_CRITERIA["APPLY_RND_FILTER"],
        help="DART 사업보고서 원문에서 파싱한 값이라 회사별 서식 차이로 일부 종목은 값을 못 가져올 "
        "수 있습니다. 체크하면 그런 종목은 조건 미달로 제외됩니다. 값 자체는 체크 여부와 무관하게 "
        "결과 표에 항상 표시됩니다.",
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
        "APPLY_RND_FILTER": apply_rnd_filter,
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
            "ROE(%)", "ROIC(%)", "부채비율(%)", "이자보상배율",
            "연구개발비율(%)", "R&D최소기준(%)", "F-Score", "기준보고서",
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
            "연구개발비율(%)": st.column_config.NumberColumn(
                "R&D비율(%)",
                help="매출액 대비 연구개발비 비율 (DART 사업보고서 원문 파싱, 가장 최근 확정 사업연도). "
                "제조업·첨단IT가 아닌 업종이거나 원문에서 값을 못 찾으면 비어 있습니다",
            ),
            "R&D최소기준(%)": st.column_config.NumberColumn(
                "R&D기준(%)",
                help="업종별 통과 기준 (KSIC 기준 제조업 5%, 첨단IT 10%). 해당 없는 업종(금융·유통·서비스 등)은 비어 있습니다",
            ),
            "F-Score": st.column_config.NumberColumn(
                "F-Score", help="Piotroski F-Score (0~9점). 참고용 — 성장주 필터링 기준에는 포함되지 않음",
            ),
            "기준보고서": st.column_config.TextColumn(
                "기준보고서", help="TTM/최근 분기 계산에 사용된 최신 DART 공시(사업/반기/분기보고서)의 접수일",
            ),
        },
    )
    financials_5y_cols = [
        "연도_5y", "매출액_5y", "영업이익_5y", "경상이익_5y", "경상이익률_5y",
        "CFO_5y", "CFI_5y", "CFF_5y", "FCF_5y",
        "부채비율_5y", "당좌비율_5y", "이자보상배율_5y", "유보율_5y",
        "매출성장률_5y", "매출채권회전율_5y", "재고자산회전율_5y",
    ]
    csv_df = df_final.drop(columns=financials_5y_cols, errors="ignore").copy()
    if "_종목명_plain" in df_final.columns:
        csv_df["종목명"] = df_final["_종목명_plain"]
    csv_bytes = csv_df.drop(columns=["_종목명_plain"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV 다운로드",
        data=csv_bytes,
        file_name="Korea_Growth_Stocks.csv",
        mime="text/csv",
    )

    st.subheader("📊 5년 재무 상세")
    if "연도_5y" not in df_final.columns:
        st.info("캐시가 아직 5년 재무 데이터를 포함하기 전 버전입니다. 캐시가 갱신되면 표시됩니다.")
    else:
        select_labels = (df_final["_종목명_plain"] + " (" + df_final["종목코드"] + ")").tolist()
        picked_label = st.selectbox("종목 선택", select_labels, key="growth_detail_stock_picker")
        picked_row = df_final.iloc[select_labels.index(picked_label)]

        years = picked_row["연도_5y"]
        if not years:
            st.info(
                "이 종목은 5년치 재무 데이터가 없습니다 "
                "(상장 5년 미만이거나, 은행·금융지주 등 계정 구조가 크게 다른 업종일 수 있습니다)."
            )
        else:
            def _ceil(val, decimals=1):
                """소수점 이하 절상(올림). math.ceil은 항상 +무한대 방향으로 반올림한다."""
                factor = 10 ** decimals
                return math.ceil(val * factor) / factor

            detail_df = pd.DataFrame({
                "연도": [str(y) for y in years],
                "매출액(억)": [_ceil(v / 100_000_000) for v in picked_row["매출액_5y"]],
                "경상이익(억)": [_ceil(v / 100_000_000) for v in picked_row["경상이익_5y"]],
                "경상이익률(%)": [_ceil(v, 2) for v in picked_row["경상이익률_5y"]],
                "영업활동CF(억)": [_ceil(v / 100_000_000) for v in picked_row["CFO_5y"]],
                "투자활동CF(억)": [_ceil(v / 100_000_000) for v in picked_row["CFI_5y"]],
                "재무활동CF(억)": [_ceil(v / 100_000_000) for v in picked_row["CFF_5y"]],
                "잉여현금흐름(억)": [_ceil(v / 100_000_000) for v in picked_row["FCF_5y"]],
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
            chart_col1, chart_col2 = st.columns(2)
            with chart_col1:
                st.line_chart(detail_df.set_index("연도")[["매출액(억)", "경상이익(억)"]])
            with chart_col2:
                st.line_chart(
                    detail_df.set_index("연도")[
                        ["영업활동CF(억)", "투자활동CF(억)", "재무활동CF(억)", "잉여현금흐름(억)"]
                    ]
                )
            st.caption(
                "경상이익은 세전이익(법인세비용차감전순이익)으로 대체한 값이며, "
                "잉여현금흐름(FCF)은 영업활동현금흐름 + 투자활동현금흐름으로 근사한 값입니다."
            )

            st.subheader("🧾 기업분석 체크리스트")
            if "부채비율_5y" not in df_final.columns:
                st.info("캐시가 아직 체크리스트 데이터를 포함하기 전 버전입니다. 캐시가 갱신되면 표시됩니다.")
            else:
                def _last(col):
                    vals = picked_row[col]
                    return vals[-1] if vals else None

                def _fmt(val, unit):
                    if val is None:
                        return "데이터 없음"
                    if unit == "회" and val == 0:
                        return "해당 없음"
                    return f"{val:,.2f}{unit}"

                def _verdict(val, ok):
                    if val is None or ok is None:
                        return "➖"
                    return "✅" if ok else "❌"

                debt_ratio = _last("부채비율_5y")
                quick_ratio = _last("당좌비율_5y")
                interest_cov = _last("이자보상배율_5y")
                reserve_ratio = _last("유보율_5y")
                revenue_growth = _last("매출성장률_5y")
                receivables_turnover = _last("매출채권회전율_5y")
                inventory_turnover = _last("재고자산회전율_5y")
                def _none_if_missing(val):
                    if val is None or (isinstance(val, float) and pd.isna(val)) or val == "":
                        return None
                    return val

                industry_name = _none_if_missing(picked_row.get("업종매칭명"))
                receivables_industry_avg = _none_if_missing(picked_row.get("매출채권회전율_업계평균"))
                inventory_industry_avg = _none_if_missing(picked_row.get("재고자산회전율_업계평균"))
                fcf_latest = _last("FCF_5y")
                fcf_latest_eok = round(fcf_latest / 100_000_000, 1) if fcf_latest is not None else None
                opm_ttm = picked_row["영업이익률(%)"]
                roe_ttm = picked_row["ROE(%)"]

                checklist_rows = [
                    ("건전성", "부채비율", debt_ratio, "%", "150% 이하",
                     debt_ratio is not None and debt_ratio <= 150,
                     "200% 이하도 괜찮으나 금리 인상기엔 150% 이하가 안전 (금융업·항공해운업 제외)"),
                    ("건전성", "당좌비율", quick_ratio, "%", "100% 이상",
                     quick_ratio is not None and quick_ratio >= 100,
                     "1년 내 갚아야 할 빚 대비 당장 현금화 가능한 자산이 충분한지"),
                    ("건전성", "이자보상배율 [핵심]", interest_cov, "회", "1.5배 이상",
                     interest_cov is not None and interest_cov >= 1.5,
                     "1 미만이면 번 돈으로 이자도 못 낸다는 뜻. 3년 연속 1 미만이면 투자 보류 권장"),
                    ("건전성", "유보율", reserve_ratio, "%", "500% 이상",
                     reserve_ratio is not None and reserve_ratio >= 500,
                     "높을수록 위기 대처 능력이 좋고 무상증자 가능성도 존재"),
                    ("수익성", "영업이익률(TTM)", opm_ttm, "%", "8~10% 이상",
                     opm_ttm is not None and opm_ttm >= 8,
                     "한국 제조업 평균 5~8%. 10% 이상이면 원가 통제력·브랜드 파워가 뛰어남"),
                    ("수익성", "ROE(TTM)", roe_ttm, "%", "8% 이상 꾸준히",
                     roe_ttm is not None and roe_ttm >= 8,
                     "주주의 돈으로 얼마나 수익을 내는지. 수년간 8~10% 이상 유지하면 우량주"),
                    ("수익성", "잉여현금흐름(FCF)", fcf_latest_eok, "억원", "플러스(+) 유지",
                     fcf_latest_eok is not None and fcf_latest_eok > 0,
                     "장부상 이익이 아닌 실제로 남은 현금. 배당·신규 투자의 원천"),
                    ("성장성", "매출 성장률", revenue_growth, "%", "5~10% 이상",
                     revenue_growth is not None and revenue_growth >= 5,
                     "물가 상승률보다 매출이 안 오르면 사실상 역성장"),
                    ("성장성", "매출채권회전율", receivables_turnover, "회",
                     f"업종평균({industry_name}) {receivables_industry_avg:,.2f}회 이상"
                     if receivables_industry_avg is not None else "동종업계 대비 높을수록 좋음 (업종 평균 데이터 없음)",
                     None if not receivables_industry_avg or not receivables_turnover
                     else receivables_turnover >= receivables_industry_avg,
                     "낮으면 물건은 팔았는데 돈을 못 받고(외상) 있다는 경고 신호. "
                     "업종 평균은 한국은행 기업경영분석(ECOS) 기준"),
                    ("성장성", "재고자산회전율", inventory_turnover, "회",
                     f"업종평균({industry_name}) {inventory_industry_avg:,.2f}회 이상"
                     if inventory_industry_avg is not None else "동종업계 대비 높을수록 좋음 (업종 평균 데이터 없음)",
                     None if not inventory_industry_avg or not inventory_turnover
                     else inventory_turnover >= inventory_industry_avg,
                     "하락 + 재고 급증 = 악성 재고 신호 (반도체·의류 등 필수 확인). "
                     "업종 평균은 한국은행 기업경영분석(ECOS) 기준"),
                ]
                checklist_df = pd.DataFrame([
                    {
                        "구분": category, "지표": name,
                        "값": _fmt(value, unit), "기준": criterion,
                        "통과": _verdict(value, ok), "체크 포인트": note,
                    }
                    for category, name, value, unit, criterion, ok, note in checklist_rows
                ])
                st.dataframe(checklist_df, use_container_width=True, hide_index=True)
                st.caption(
                    "체크 항목은 가장 최근 확정된 사업연도(영업이익률·ROE는 TTM) 기준이며, "
                    "매출채권/재고자산 회전율은 업종 평균 데이터가 없어 통과 여부를 판정하지 않고 값만 보여줍니다. "
                    "'해당 없음'은 재고·매출채권이 거의 없는 업종(서비스업 등)일 수 있습니다."
                )

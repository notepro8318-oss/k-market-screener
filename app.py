import streamlit as st

from screener import DEFAULT_FILTER_CRITERIA, DEFAULT_TARGET_YEAR, run_pipeline
from ui_common import get_dart_api_key

st.set_page_config(page_title="K-Market Value Screener", page_icon="📈", layout="wide")

st.title("📈 한국 저평가 우량주 스크리너")
st.caption("시가총액 · PER · PBR · 수익성(OPM/ROA/ROE) · Piotroski F-Score 기반 2단계 필터링")


with st.sidebar:
    st.header("⚙️ 스크리닝 조건")

    dart_api_key = get_dart_api_key()

    target_year = st.number_input(
        "결산 사업보고서 연도", min_value=2015, max_value=2100,
        value=DEFAULT_TARGET_YEAR, step=1,
    )

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

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True)

if run_clicked:
    if not dart_api_key:
        st.error("OpenDART API Key를 입력해주세요.")
        st.stop()

    status_box = st.empty()
    progress_bar = st.progress(0.0)

    def log(msg):
        text = str(msg).strip()
        if text:
            status_box.info(text)

    def progress_cb(stage, done, total):
        progress_bar.progress(done / total if total else 0.0)

    with st.spinner("스크리닝 실행 중... (전종목 스캔 + 재무제표 분석, 수 분 소요될 수 있습니다)"):
        df_final = run_pipeline(
            dart_api_key, int(target_year), criteria, log=log, progress_cb=progress_cb
        )

    progress_bar.empty()
    status_box.empty()

    if df_final.empty:
        st.warning("조건을 모두 만족하는 종목이 없습니다. 조건을 완화한 뒤 다시 시도해보세요.")
    else:
        st.success(f"{len(df_final)}개 종목이 조건을 통과했습니다.")
        st.dataframe(df_final, use_container_width=True, hide_index=True)
        csv_bytes = df_final.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 CSV 다운로드",
            data=csv_bytes,
            file_name="Korea_Value_HighQuality_Stocks.csv",
            mime="text/csv",
        )
else:
    st.info("왼쪽에서 조건을 설정한 뒤 **스크리닝 실행** 버튼을 눌러주세요. 전체 스크리닝은 수 분 정도 소요됩니다.")

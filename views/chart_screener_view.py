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


def _stage2_evaluate(row, lookback_bars, vol_ratio_threshold_pct, value_floor):
    """
    2단계(최근 N봉 내 "거래량 VMA20대비배수" + "거래대금" 동시충족)를 판정해 "기준봉"을 찾는다.
    통과한 날이 있으면 그중 가장 최근 날짜(=기준봉)를, 없으면 위반폭(%)이 가장 작았던(=가장
    근접한) 날을 함께 반환한다 — 위반폭은 두 조건 각각의 미달률(%) 합으로, 0이면 그날 통과.
    "2단계_근접일_idx"는 최근30봉 배열 안에서 그 날짜의 위치로, 3단계의 캔들 윗꼬리(#7)·눌림목
    거래량(#9) 계산이 같은 배열에서 그 위치를 찾아 이어서 계산하는 데 쓰인다.
    """
    dates = row.get("최근30봉_일자") or []
    ratios = row.get("최근30봉_거래량배수_VMA20대비") or []
    values = row.get("최근30봉_거래대금") or []
    n = min(lookback_bars, len(dates))
    start = len(dates) - n

    best = None  # (idx, date, ratio, value, violation)
    for idx in range(start, len(dates)):
        d, r, v = dates[idx], ratios[idx], values[idx]
        if r is None or v is None:
            continue
        vol_viol = max(0.0, vol_ratio_threshold_pct - r * 100) / vol_ratio_threshold_pct * 100
        val_viol = max(0.0, 1 - v / value_floor) * 100 if value_floor > 0 else 0.0
        viol = vol_viol + val_viol
        if best is None or viol < best[4] or (viol == best[4] and d > best[1]):
            best = (idx, d, r, v, viol)

    if best is None:
        return pd.Series({
            "2단계_충족": False, "2단계_근접일": None, "2단계_근접일_idx": None,
            "2단계_근접일_거래량배수": None, "2단계_근접일_거래대금": None, "2단계_위반폭": 999.0,
        })
    return pd.Series({
        "2단계_충족": best[4] <= 0, "2단계_근접일": best[1], "2단계_근접일_idx": best[0],
        "2단계_근접일_거래량배수": best[2], "2단계_근접일_거래대금": best[3], "2단계_위반폭": best[4],
    })


def _stage37_evaluate(row):
    """
    #7(기준봉 캔들의 윗꼬리/몸통 비율)과 #9(기준봉 익일부터 어제까지 눌림목 구간의 평균거래량
    ÷ 기준봉 거래량)를 계산한다. 기준봉이 없거나(2단계 미충족), 눌림목 구간이 비어있으면
    (기준봉이 오늘이거나 어제라 T+1~T-1 구간에 날짜가 없으면) #9는 "구간무효"로 표시하고
    — 사용자 확정에 따라 이 경우 #9는 통과로 인정하지 않는다.
    """
    idx = row.get("2단계_근접일_idx")
    passed = row.get("2단계_충족")
    wick_arr = row.get("최근30봉_윗꼬리몸통비율(%)") or []
    vol_arr = row.get("최근30봉_거래량") or []

    if not passed or idx is None:
        return pd.Series({"7단계_윗꼬리비율(%)": None, "9단계_눌림목거래량비율(%)": None, "9단계_구간유효": False})

    wick_ratio = wick_arr[idx] if idx < len(wick_arr) else None

    n = len(vol_arr)
    pullback = [v for v in vol_arr[idx + 1: n - 1] if v is not None]  # T+1 ~ 어제(T-1), 오늘 제외
    base_volume = vol_arr[idx] if idx < len(vol_arr) else None

    if not pullback or base_volume is None or base_volume <= 0:
        return pd.Series({"7단계_윗꼬리비율(%)": wick_ratio, "9단계_눌림목거래량비율(%)": None, "9단계_구간유효": False})

    avg_pullback_vol = sum(pullback) / len(pullback)
    return pd.Series({
        "7단계_윗꼬리비율(%)": wick_ratio,
        "9단계_눌림목거래량비율(%)": round(avg_pullback_vol / base_volume * 100, 2),
        "9단계_구간유효": True,
    })


def _compute_near_miss(df, min_consecutive_months, min_gap_from_high_pct, max_gap_from_high_pct,
                        max_monthly_ma5_disparity_pct, max_daily_ma60_disparity_pct,
                        max_wick_body_pct, max_pullback_volume_pct, require_stage4, top_n=15):
    """
    조건을 통과하는 종목이 하나도 없을 때, 지금 설정된 조건 값 기준으로 "가장 근접한" 종목을
    순위로 매긴다. 각 조건마다 정규화된 위반폭(%)을 계산해 합산한 뒤(적을수록 근접), 그 합으로
    정렬한다. 실패한 조건 개수만으로는 "몇 개 통과했나"의 착시가 생길 수 있어(예: 나머지는 다
    통과했지만 딱 하나가 크게 못 미치는 대형주가, 여러 개를 조금씩 못 미치는 종목보다 항상
    "더 가깝다"고 잘못 보일 수 있음) 위반폭 합을 기본 정렬 기준으로 쓴다.

    #7·#9는 기준봉(2단계 통과일)이 있어야만 의미가 있는 조건이라, 2단계 자체가 아직 통과하지
    못한 종목(대다수의 근접 후보)에는 0(중립)으로 채워 2단계 위반폭과 중복으로 불이익을 주지
    않는다.
    """
    d = df.copy()
    band_width = max(max_gap_from_high_pct - min_gap_from_high_pct, 1e-9)

    d["v1"] = ((min_consecutive_months - d["5개월선_연속상회월수"]).clip(lower=0)) / min_consecutive_months * 100
    d["c1_통과"] = d["v1"] <= 0

    d["v2"] = d["2단계_위반폭"]
    d["c2_통과"] = d["2단계_충족"]

    d["v3"] = (d["120봉저점대비_상승률(%)"] - d["_rise_cap"]).clip(lower=0) / d["_rise_cap"] * 100
    d["c3_통과"] = d["v3"] <= 0

    below = (min_gap_from_high_pct - d["250봉고점대비_괴리율(%)"]).clip(lower=0)
    above = (d["250봉고점대비_괴리율(%)"] - max_gap_from_high_pct).clip(lower=0)
    d["v4"] = (below + above) / band_width * 100
    d["c4_통과"] = d["v4"] <= 0

    d["v5"] = (d["월봉5MA이격도(%)"] - max_monthly_ma5_disparity_pct).clip(lower=0) / max_monthly_ma5_disparity_pct * 100
    d["c5_통과"] = d["v5"] <= 0

    d["v6"] = (d["일봉60MA이격도(%)"] - max_daily_ma60_disparity_pct).clip(lower=0) / max_daily_ma60_disparity_pct * 100
    d["c6_통과"] = d["v6"] <= 0

    has_junggibong = d["2단계_충족"]
    wick_viol = (d["7단계_윗꼬리비율(%)"] - max_wick_body_pct).clip(lower=0) / max_wick_body_pct * 100
    d["v7"] = wick_viol.where(has_junggibong & d["7단계_윗꼬리비율(%)"].notna(), 0.0)
    d["c7_통과"] = (~has_junggibong) | (d["7단계_윗꼬리비율(%)"].notna() & (d["7단계_윗꼬리비율(%)"] <= max_wick_body_pct))

    pullback_viol = (d["9단계_눌림목거래량비율(%)"] - max_pullback_volume_pct).clip(lower=0) / max_pullback_volume_pct * 100
    d["v9"] = pullback_viol.where(has_junggibong & d["9단계_구간유효"], 0.0)
    d["c9_통과"] = (~has_junggibong) | (d["9단계_구간유효"] & (d["9단계_눌림목거래량비율(%)"] <= max_pullback_volume_pct))

    if require_stage4:
        gap_to_ma5 = (100 - d["당일5일선이격도(%)"]).clip(lower=0)
        gap_to_bullish = (-d["당일시가대비등락률(%)"]).clip(lower=0)
        d["v10"] = pd.concat([gap_to_ma5, gap_to_bullish], axis=1).min(axis=1)
        d["c10_통과"] = d["v10"] <= 0
    else:
        d["v10"] = 0.0
        d["c10_통과"] = True

    cond_cols = ["c1_통과", "c2_통과", "c3_통과", "c4_통과", "c5_통과", "c6_통과", "c7_통과", "c9_통과", "c10_통과"]
    viol_cols = ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v9", "v10"]
    d["실패조건수"] = len(cond_cols) - d[cond_cols].sum(axis=1)
    d["종합근접도점수"] = d[viol_cols].sum(axis=1)

    near = d.sort_values("종합근접도점수").head(top_n).copy()
    near["시가총액(억)"] = (near["시가총액"] / 100_000_000).round(0)
    near["_종목명_plain"] = near["종목명"]
    near["종목명"] = near.apply(
        lambda r: f"https://finance.naver.com/item/main.naver?code={r['종목코드']}#{r['_종목명_plain']}",
        axis=1,
    )
    return near


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
        help="이 기간 안에 거래량 배수·거래대금 조건을 동시에 만족한 날(기준봉)이 하루라도 있으면 통과",
    )
    min_volume_ratio_pct = st.number_input(
        "거래량 배수 (%) 이상 (20일 평균(VMA20) 대비)", min_value=100.0, value=300.0, step=50.0,
        help="그날 거래량 ÷ 직전 20거래일 평균 거래량(VMA20, 당일 포함) × 100",
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
    st.caption("120거래일(약 6개월) 저점 대비 상승률 (%) 초과 시 제외 — 시총 규모별 차등 (대형주/소형주 기준은 2단계와 동일)")
    max_rise_from_low_large_pct = st.number_input(
        "대형주 기준 (%) 초과 시 제외", min_value=0.0, value=50.0, step=5.0,
    )
    max_rise_from_low_small_pct = st.number_input(
        "소형주 기준 (%) 초과 시 제외", min_value=0.0, value=70.0, step=5.0,
    )
    st.caption("250거래일(약 52주) 신고가 대비 현재가 괴리율 (%) — 이 범위 밖이면 제외")
    min_gap_from_high_pct = st.number_input(
        "괴리율 하한 (%)", value=-35.0, step=5.0,
        help="음수일수록 신고가에서 더 멀리 떨어진 상태. 이보다 더 낮으면(더 많이 빠졌으면) 제외",
    )
    max_gap_from_high_pct = st.number_input(
        "괴리율 상한 (%)", value=-15.0, step=5.0,
        help="0에 가까울수록 신고가에 근접. 이보다 높으면(신고가에 너무 가까우면) 제외",
    )
    max_monthly_ma5_disparity_pct = st.number_input(
        "월봉 5MA 이격도 (%) 초과 시 제외", min_value=100.0, value=115.0, step=1.0,
        help="월봉 종가 ÷ 5개월 이동평균선 × 100",
    )
    max_daily_ma60_disparity_pct = st.number_input(
        "일봉 60MA 이격도 (%) 초과 시 제외", min_value=100.0, value=112.0, step=1.0,
        help="현재가 ÷ 일봉 60일 이동평균선 × 100",
    )
    max_wick_body_pct = st.number_input(
        "기준봉 윗꼬리/몸통 비율 (%) 초과 시 제외", min_value=0.0, value=100.0, step=10.0,
        help="기준봉(2단계 이벤트일) 캔들의 (고가-max(시가,종가)) ÷ |종가-시가| × 100. "
        "100% 초과면 윗꼬리가 몸통보다 길다는 뜻으로, 고점 매도 압력(세력 이탈) 신호로 봅니다.",
    )
    max_pullback_volume_pct = st.number_input(
        "눌림목 평균거래량/기준봉거래량 비율 (%) 초과 시 제외", min_value=0.0, value=30.0, step=5.0,
        help="기준봉 익일(T+1)부터 어제(T-1)까지의 일평균 거래량 ÷ 기준봉 거래량 × 100. "
        "기준봉이 오늘이거나 어제라 눌림목 구간이 아직 없으면 검증 불가로 제외됩니다.",
    )

    st.header("⚙️ 4단계 · 재반등 확인")
    require_stage4 = st.checkbox(
        "당일 5일선 이상 또는 당일 양봉 조건 적용", value=True,
        help="눌림목 이후 재반등 신호 — 당일 종가가 일봉 5일 이동평균선 이상이거나, 당일 종가가 시가보다 높으면(양봉) 통과",
    )

    market = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True, key="chart_market")

    run_clicked = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True, key="chart_run")

_RESULT_COLUMN_CONFIG = {
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
    "2단계_근접일": st.column_config.TextColumn(
        "기준봉일", help="최근 조회 구간 내에서 거래량·거래대금 조건에 가장 근접했던(통과했다면 그중 최근) 날짜",
    ),
    "2단계_근접일_거래량배수": st.column_config.NumberColumn(
        "기준봉 거래량배수", format="%.1f배", help="기준봉일의 거래량 ÷ 직전 20거래일 평균 거래량(VMA20)",
    ),
    "2단계_근접일_거래대금": st.column_config.NumberColumn(
        "기준봉 거래대금(원)", help="기준봉일의 거래대금 (종가 × 거래량으로 근사)",
    ),
    "120봉저점대비_상승률(%)": st.column_config.NumberColumn(
        "120봉저점 대비(%)", format="%.1f%%",
        help="최근 120거래일(약 6개월) 저점 대비 현재가 상승률. 너무 높으면 이미 크게 오른 뒤(상투)로 보고 제외 대상",
    ),
    "250봉고점대비_괴리율(%)": st.column_config.NumberColumn(
        "52주신고가 대비(%)", format="%.1f%%",
        help="최근 250거래일(약 52주) 신고가 대비 현재가 괴리율. 음수(-)가 클수록 신고가에서 더 멀리 떨어진 상태",
    ),
    "월봉5MA이격도(%)": st.column_config.NumberColumn(
        "월봉5MA 이격도(%)", format="%.1f%%",
        help="월봉 종가 ÷ 5개월 이동평균선 × 100. 100%보다 크게 벌어질수록 평균회귀 급락 위험",
    ),
    "일봉60MA이격도(%)": st.column_config.NumberColumn(
        "일봉60MA 이격도(%)", format="%.1f%%",
        help="현재가 ÷ 일봉 60일 이동평균선 × 100. 100%보다 크게 벌어질수록 평균회귀 급락 위험",
    ),
    "7단계_윗꼬리비율(%)": st.column_config.NumberColumn(
        "기준봉 윗꼬리/몸통(%)", format="%.0f%%",
        help="기준봉 캔들의 윗꼬리 ÷ 몸통 × 100. 100% 초과면 고점에서 매도 압력에 밀린 캔들(세력 이탈 신호)",
    ),
    "9단계_눌림목거래량비율(%)": st.column_config.NumberColumn(
        "눌림목거래량비율(%)", format="%.0f%%",
        help="기준봉 익일부터 어제까지 일평균 거래량 ÷ 기준봉 거래량 × 100. 낮을수록 눌림목 구간에서 거래량이 잘 마른 것",
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
    "실패조건수": st.column_config.NumberColumn(
        "실패조건수", help="1~4단계 총 9개 세부조건 중 통과하지 못한 개수 (적을수록 근접)",
    ),
    "종합근접도점수": st.column_config.NumberColumn(
        "근접도점수", format="%.1f", help="각 조건의 미달률(%)을 합산한 값 — 작을수록 조건 값에 더 가까움",
    ),
}

_RESULT_COLUMN_ORDER = [
    "종목코드", "종목명", "시장구분", "시총구분", "시가총액(억)", "현재가",
    "5개월선_연속상회월수",
    "2단계_근접일", "2단계_근접일_거래량배수", "2단계_근접일_거래대금",
    "120봉저점대비_상승률(%)", "250봉고점대비_괴리율(%)", "월봉5MA이격도(%)", "일봉60MA이격도(%)",
    "7단계_윗꼬리비율(%)", "9단계_눌림목거래량비율(%)",
    "당일5일선이상", "당일양봉", "기준일",
]

if run_clicked:
    df = pd.DataFrame(cache["rows"])
    if market != "전체":
        df = df[df["시장구분"] == market]

    required_cols = [
        "5개월선_연속상회월수", "시가총액", "120봉저점대비_상승률(%)", "250봉고점대비_괴리율(%)",
        "월봉5MA이격도(%)", "일봉60MA이격도(%)", "당일5일선이상", "당일양봉",
        "당일5일선이격도(%)", "당일시가대비등락률(%)",
    ]
    df = df.dropna(subset=required_cols)

    large_cap_threshold = large_cap_threshold_eok * 100_000_000
    min_value_large = min_value_large_eok * 100_000_000
    min_value_small = min_value_small_eok * 100_000_000

    is_large = df["시가총액"] >= large_cap_threshold
    df["_value_floor"] = is_large.map({True: min_value_large, False: min_value_small})
    df["_rise_cap"] = is_large.map({True: max_rise_from_low_large_pct, False: max_rise_from_low_small_pct})

    stage2 = df.apply(lambda r: _stage2_evaluate(r, lookback_bars, min_volume_ratio_pct, r["_value_floor"]), axis=1)
    df = pd.concat([df, stage2], axis=1)

    stage37 = df.apply(_stage37_evaluate, axis=1)
    df = pd.concat([df, stage37], axis=1)

    cond = (
        (df["5개월선_연속상회월수"] >= min_consecutive_months)
        & (df["2단계_충족"])
        & (df["120봉저점대비_상승률(%)"] <= df["_rise_cap"])
        & (df["250봉고점대비_괴리율(%)"] >= min_gap_from_high_pct)
        & (df["250봉고점대비_괴리율(%)"] <= max_gap_from_high_pct)
        & (df["월봉5MA이격도(%)"] <= max_monthly_ma5_disparity_pct)
        & (df["일봉60MA이격도(%)"] <= max_daily_ma60_disparity_pct)
        & (df["7단계_윗꼬리비율(%)"].notna())
        & (df["7단계_윗꼬리비율(%)"] <= max_wick_body_pct)
        & (df["9단계_구간유효"])
        & (df["9단계_눌림목거래량비율(%)"] <= max_pullback_volume_pct)
    )
    if require_stage4:
        cond = cond & (df["당일5일선이상"] | df["당일양봉"])

    df_screened = df[cond].copy()
    if not df_screened.empty:
        df_screened = df_screened.sort_values("2단계_근접일_거래량배수", ascending=False)
        df_screened["시가총액(억)"] = (df_screened["시가총액"] / 100_000_000).round(0)
        df_screened["_종목명_plain"] = df_screened["종목명"]
        df_screened["종목명"] = df_screened.apply(
            lambda r: f"https://finance.naver.com/item/main.naver?code={r['종목코드']}#{r['_종목명_plain']}",
            axis=1,
        )
        near_miss_df = None
    else:
        near_miss_df = _compute_near_miss(
            df, min_consecutive_months, min_gap_from_high_pct, max_gap_from_high_pct,
            max_monthly_ma5_disparity_pct, max_daily_ma60_disparity_pct,
            max_wick_body_pct, max_pullback_volume_pct, require_stage4,
        )
    st.session_state["chart_screening_df"] = df_screened
    st.session_state["chart_near_miss_df"] = near_miss_df

df_final = st.session_state.get("chart_screening_df")
near_miss_final = st.session_state.get("chart_near_miss_df")

if df_final is None:
    st.info("왼쪽에서 조건을 설정한 뒤 **스크리닝 실행** 버튼을 눌러주세요.")
elif df_final.empty:
    st.warning("조건을 모두 만족하는 종목이 없습니다. 조건을 완화한 뒤 다시 시도해보세요.")
else:
    st.success(f"{len(df_final)}개 종목이 조건을 통과했습니다.")
    st.caption("기준봉 거래량 배수 높은 순으로 정렬되어 있습니다.")
    st.dataframe(
        df_final,
        use_container_width=True,
        hide_index=True,
        column_order=_RESULT_COLUMN_ORDER,
        column_config=_RESULT_COLUMN_CONFIG,
    )
    csv_df = df_final.drop(columns=["_종목명_plain", "_value_floor", "_rise_cap"], errors="ignore").copy()
    if "_종목명_plain" in df_final.columns:
        csv_df["종목명"] = df_final["_종목명_plain"]
    csv_bytes = csv_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV 다운로드",
        data=csv_bytes,
        file_name="Korea_Chart_Screened_Stocks.csv",
        mime="text/csv",
    )

if df_final is not None and df_final.empty and near_miss_final is not None and not near_miss_final.empty:
    st.divider()
    st.markdown("### 🔎 조건에 가장 근접한 종목")
    st.caption(
        "지금 설정된 조건 값 기준으로, 9개 세부조건(추세·수급·상투배제 5개·재반등)의 미달률을 합산해 "
        "가장 가까운 순으로 정렬했습니다. 실패조건수가 적어도 그 조건을 크게 못 미치면 근접도점수가 "
        "나빠질 수 있어(예: 나머지는 다 통과해도 거래량 급증이 전혀 없는 대형주), 근접도점수를 기본 "
        "정렬 기준으로 씁니다. 기준봉(2단계)이 아직 없는 종목은 #7·#9(기준봉 캔들·눌림목 거래량) "
        "위반폭을 0으로 두어 2단계 미달과 중복으로 불이익을 주지 않습니다."
    )
    st.dataframe(
        near_miss_final,
        use_container_width=True,
        hide_index=True,
        column_order=["실패조건수", "종합근접도점수"] + _RESULT_COLUMN_ORDER,
        column_config=_RESULT_COLUMN_CONFIG,
    )

with st.expander("ℹ️ 조건 설명 (1~4단계)"):
    st.markdown(
        """
- **1단계 · 장기 추세 필터**: 월봉 종가가 5개월 이동평균선 위에서 연속으로 머문 개월 수(이번달 포함, 기본 3개월 이상)
- **2단계 · 수급 유효성 검증**: 최근 N봉(기본 20봉) 이내에 "그날 거래량이 20일 평균(VMA20) 대비 300% 이상"이면서
  "그날 거래대금이 시총 규모별 기준(대형주 1,000억/소형주 500억) 이상"인 날("기준봉")이 동시에 1회 이상 있었는지 확인
  (당일이 아니어도, 최근 며칠 내 그런 수급 이벤트가 있었으면 통과 — 이벤트 발생일과 당일 시점의 다른 조건이
  같은 날일 필요는 없습니다)
- **3단계 · 상투 분산 배제**:
  - 120거래일(약 6개월) 저점 대비 상승률(시총 규모별 차등 — 대형주 기본 50% 이하, 소형주 기본 70% 이하)
  - 250거래일(약 52주) 신고가 대비 괴리율이 기본 -35%~-15% 범위 안에 있는지 (너무 신고가에 가까우면
    상투일 수 있고, 너무 멀면 추세 자체가 약한 것으로 봄)
  - 월봉 5MA 이격도(기본 115% 이하), 일봉 60MA 이격도(기본 112% 이하) — 평균회귀 급락 리스크 회피
  - 기준봉(2단계 이벤트일) 캔들의 윗꼬리/몸통 비율(기본 100% 이하) — 윗꼬리가 길면 고점에서 매도 압력에
    밀린 캔들(세력 이탈 신호)로 봄
  - 기준봉 익일부터 어제까지(눌림목 구간)의 일평균 거래량이 기준봉 거래량의 기본 30% 이하인지 — 눌림목
    구간에서 거래량이 잘 말라야 물량이 아직 세력 손에 남아있다고 봄 (기준봉이 오늘·어제라 눌림목 구간이
    아직 없으면 검증 불가로 제외)
- **4단계 · 재반등 확인**: 당일 종가가 일봉 5일 이동평균선 이상이거나, 당일 종가가 시가보다 높으면(양봉) 통과
  — 눌림목 이후 재반등하는 흐름인지 확인
- **조건에 가장 근접한 종목**: 통과 종목이 0건일 때만 하단에 표시됩니다. 각 조건을 얼마나 못 미쳤는지(%)를
  합산한 점수로 순위를 매긴 것으로, 실제로 조건을 통과한 것은 아닙니다.
        """
    )

with st.expander("📖 청산 프로토콜 (참고용 — 자동 판정하지 않음)"):
    st.markdown(
        """
이 화면은 신규 진입 후보를 찾는 스크리너이고, 실제로 어떤 종목을 얼마에 보유 중인지는 알 수
없어 아래 규칙은 자동 판정하지 않습니다. 직접 보유 종목에 적용해보세요.
- **기계적 손절 (Stop-Loss)**: 진입 가격 대비 -6.5% 장중 터치 즉시 → 보유 수량 100% 전량 매도
- **1차 확정 익절 (Take-Profit)**: 진입 가격 대비 +25.0% 도달 시 → 보유 수량의 30% 매도
- **단기 추세 청산 (Profit Protection)**: 일봉 종가가 20일 이동평균선을 하향 이탈 시 →
  잔여 수량의 50%(전체의 35%) 매도
- **최종 대추세 종료 (Final Exit)**: 월봉 종가가 5개월 이동평균선을 하향 이탈 시 →
  남은 잔여 물량(전체의 35%) 전량 매도
        """
    )

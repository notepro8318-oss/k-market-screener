"""
"차트 분석 종목 찾기" - 사용자가 최종 확정한 4단계 시스템 트레이딩 스크리닝 모듈.

1단계(장기 추세): 월봉 종가가 5개월 이동평균선 위에서 3개월 이상 연속 유지
2단계(수급 유효성): 최근 20거래일(봉) 이내에 "그날 거래량이 직전 20일 평균 거래량(VMA20) 대비
                  300% 이상"이면서 "그날 거래대금이 시총 규모별 기준(대형주 1,000억/소형주
                  500억) 이상"인 날이 동시에 1회 이상 있었는지
3단계(상투 분산 배제): 120거래일(약 6개월) 저점 대비 현재가 상승률, 월봉 5MA/일봉 60MA 이격도
4단계(재반등 확인): 당일 종가가 일봉 5일 이동평균선 이상이거나, 당일이 양봉(종가>시가)

기존에 있던 "청산 프로토콜"(손절/익절/추세청산)은 실제 보유 종목의 매입가를 알아야 의미가
있는 포지션 관리 로직이라 코드가 아닌 화면 설명 텍스트로만 안내한다(views/chart_screener_view.py).

DART 재무데이터가 필요 없어 OpenDART의 해외 IP 차단 문제는 없지만, 코스피·코스닥 전 종목
(약 2,500개)의 일봉 데이터를 매번 새로 받아야 해서 Streamlit 요청 중에 실시간으로 돌리기엔
너무 느리고 불안정하다(수분 이상 소요, 아웃바운드 연결 다수). 그래서 VKOSPI/코스피 PBR/
산업군 MDD와 동일한 패턴으로 로컬 배치(crawl_chart_screener.py)에서 하루 한 번 전종목을
수집·평가해 data/chart_screener_cache.json에 저장하고, Streamlit 앱은 그 캐시만 읽는다.

캐시에는 최종 통과 여부가 아니라 종목별 계산된 원본 지표만 저장한다. 특히 2단계는 "최근 20봉
중 1회 이상"처럼 화면에서 조절 가능해야 하는 구간 조건이라, 배치 단계에서 20봉으로 미리
확정하지 않고 여유 있게 30봉치의 일별 (거래량_VMA20대비배수, 거래대금) 원본 시계열을 저장해
화면 슬라이더로 봉 수·배수·거래대금 기준을 자유롭게 조절할 수 있게 한다.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import FinanceDataReader as fdr
import pandas as pd
import requests

# FinanceDataReader의 개별 종목 조회(NaverDailyReader._naver_data_reader)는
# requests.get()에 timeout을 전혀 지정하지 않는다. 서버가 응답을 늦게 주거나 아예
# 응답이 없으면(연결 거부와 달리) 그 워커 스레드가 영원히 멈춰버려, 전종목을 병렬로
# 도는 crawl_universe()가 결국 모든 워커가 멈춰 진행이 정지되는 사례가 있었다
# (screener.py가 opendartreader에 적용한 것과 동일한 패턴으로 해결).
_DEFAULT_HTTP_TIMEOUT = (10, 25)  # (연결, 응답) 초
_orig_session_request = requests.Session.request


def _session_request_with_default_timeout(self, method, url, **kwargs):
    kwargs.setdefault("timeout", _DEFAULT_HTTP_TIMEOUT)
    return _orig_session_request(self, method, url, **kwargs)


requests.Session.request = _session_request_with_default_timeout

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CHART_SCREENER_CACHE = os.path.join(DATA_DIR, "chart_screener_cache.json")

# 월봉 5개월선 연속 상회 판정, 120거래일 저점, 일봉 60일 이동평균 계산에 필요한 만큼 넉넉히
# 24개월(2년)치 일봉을 받는다. NaverDailyReader는 요청 기간과 무관하게 매번 최대 6000일치를
# 내려받은 뒤 클라이언트에서 잘라내는 구조라, 기간을 늘려도 크롤링 속도에는 영향이 없다.
_HISTORY_MONTHS = 24

# 시가총액 1조원을 대형주/소형주 경계로 삼는다(화면에서 조절 가능).
LARGE_CAP_THRESHOLD = 1_000_000_000_000

# 2단계 "최근 20봉" 조건을 화면에서 20~30봉 범위로 조절할 수 있도록, 배치에서는 여유 있게
# 30봉치를 저장해둔다.
_LOOKBACK_STORE_BARS = 30


def fetch_universe():
    """
    코스피·코스닥 상장 전 종목(종목코드/종목명/시장구분/시가총액)을 가져온다.
    screener.run_first_stage_screening과 동일하게 KOSDAQ GLOBAL은 KOSDAQ으로 합치고
    KONEX는 이 스크리너의 대상이 아니므로 제외한다.
    """
    df_listing = fdr.StockListing("KRX")
    df_listing = df_listing.set_index("Code")[["Name", "Market", "Marcap"]]
    df_listing.columns = ["종목명", "시장구분", "시가총액"]
    df_listing["시장구분"] = df_listing["시장구분"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    return df_listing[df_listing["시장구분"].isin(["KOSPI", "KOSDAQ"])]


def evaluate_stock(ticker, name, market, marcap, df):
    """
    종목 하나의 일봉 OHLCV(df, DatetimeIndex + Open/Close/Volume 컬럼)로부터
    1~4단계 판정에 필요한 원본 지표를 계산해 반환한다. 데이터가 부족하면 None.
    """
    required_cols = {"Open", "Close", "Volume"}
    if df is None or df.empty or not required_cols.issubset(df.columns):
        return None

    df = df.sort_index()
    open_ = df["Open"].astype(float)
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    trading_value = close * volume

    # --- 1단계: 월봉 5개월선 연속 상회 개월수 ---
    monthly_close = close.resample("ME").last().dropna()
    if len(monthly_close) < 6:  # 5개월선 계산(5개) + 최소 1개월 판정
        return None
    ma5_monthly = monthly_close.rolling(5).mean()

    streak = 0
    for m_close, m_ma5 in zip(monthly_close.iloc[::-1], ma5_monthly.iloc[::-1]):
        if pd.isna(m_ma5):
            break
        if m_close > m_ma5:
            streak += 1
        else:
            break

    if len(close) < 120:  # 120봉 저점 계산에 필요한 최소치
        return None

    # --- 2단계: 최근 N봉(최대 30봉 저장) 일별 (거래량 VMA20대비배수, 거래대금) 원본 시계열 ---
    # VMA20(20일 거래량 이동평균)은 일반적인 차트 지표와 동일하게 당일 거래량을 포함한
    # 20거래일 평균으로 계산한다(HTS/차트에서 그려지는 거래량 이동평균선과 동일한 정의).
    vma20 = volume.rolling(20).mean()
    vol_ratio_vs_vma20 = volume / vma20
    recent_idx = close.index[-_LOOKBACK_STORE_BARS:]
    recent_dates = [d.strftime("%Y-%m-%d") for d in recent_idx]
    recent_vol_ratio = [
        round(float(v), 2) if pd.notna(v) else None
        for v in vol_ratio_vs_vma20.reindex(recent_idx)
    ]
    recent_value = [
        round(float(v)) if pd.notna(v) else None
        for v in trading_value.reindex(recent_idx)
    ]

    # --- 3단계: 120거래일(약 6개월) 저점 대비 상승률 ---
    low_120 = close.iloc[-120:].min()
    latest_close = close.iloc[-1]
    rise_from_120low_pct = (latest_close / low_120 - 1) * 100 if low_120 > 0 else None

    # --- 3단계: 이동평균 이격도(%) = 종가 / 이동평균 * 100 ---
    latest_ma5_monthly = ma5_monthly.iloc[-1]
    monthly_ma5_disparity_pct = (
        (monthly_close.iloc[-1] / latest_ma5_monthly * 100)
        if pd.notna(latest_ma5_monthly) and latest_ma5_monthly > 0 else None
    )
    ma60_daily = close.rolling(60).mean()
    latest_ma60_daily = ma60_daily.iloc[-1]
    daily_ma60_disparity_pct = (
        (latest_close / latest_ma60_daily * 100)
        if pd.notna(latest_ma60_daily) and latest_ma60_daily > 0 else None
    )

    # --- 4단계: 당일 5일선 이상 여부 / 당일 양봉 여부 (이격도·등락률은 "근접 종목" 산출용 연속값) ---
    ma5_daily = close.rolling(5).mean()
    latest_ma5_daily = ma5_daily.iloc[-1]
    daily_ma5_disparity_pct = (
        (latest_close / latest_ma5_daily * 100)
        if pd.notna(latest_ma5_daily) and latest_ma5_daily > 0 else None
    )
    above_ma5_daily = bool(daily_ma5_disparity_pct is not None and daily_ma5_disparity_pct >= 100)
    latest_open = open_.iloc[-1]
    open_to_close_pct = (
        (latest_close / latest_open - 1) * 100
        if pd.notna(latest_open) and latest_open > 0 else None
    )
    bullish_candle = bool(open_to_close_pct is not None and open_to_close_pct > 0)

    latest_date = close.index[-1]
    latest_value = trading_value.iloc[-1]
    marcap = float(marcap) if pd.notna(marcap) else None

    return {
        "종목코드": ticker,
        "종목명": name,
        "시장구분": market,
        "시가총액": round(marcap) if marcap is not None else None,
        "시총구분": ("대형주" if marcap is not None and marcap >= LARGE_CAP_THRESHOLD else "소형주"),
        "5개월선_연속상회월수": streak,
        f"최근{_LOOKBACK_STORE_BARS}봉_일자": recent_dates,
        f"최근{_LOOKBACK_STORE_BARS}봉_거래량배수_VMA20대비": recent_vol_ratio,
        f"최근{_LOOKBACK_STORE_BARS}봉_거래대금": recent_value,
        "120봉저점대비_상승률(%)": round(rise_from_120low_pct, 2) if rise_from_120low_pct is not None else None,
        "월봉5MA이격도(%)": round(monthly_ma5_disparity_pct, 2) if monthly_ma5_disparity_pct is not None else None,
        "일봉60MA이격도(%)": round(daily_ma60_disparity_pct, 2) if daily_ma60_disparity_pct is not None else None,
        "당일5일선이상": above_ma5_daily,
        "당일5일선이격도(%)": round(daily_ma5_disparity_pct, 2) if daily_ma5_disparity_pct is not None else None,
        "당일양봉": bullish_candle,
        "당일시가대비등락률(%)": round(open_to_close_pct, 2) if open_to_close_pct is not None else None,
        "당일_거래대금": round(latest_value) if pd.notna(latest_value) else None,
        "현재가": round(latest_close) if pd.notna(latest_close) else None,
        "기준일": latest_date.strftime("%Y-%m-%d"),
    }


def crawl_universe(max_workers=15, log=print):
    """
    전종목 일봉을 병렬로 받아 evaluate_stock()으로 평가한 결과 리스트를 반환한다.
    """
    universe = fetch_universe()
    end = pd.Timestamp.today()
    start = end - pd.DateOffset(months=_HISTORY_MONTHS)
    total = len(universe)
    log(f"▶ 전종목 {total}개 일봉 수집 및 차트 조건 평가 시작...")

    def _worker(ticker):
        row = universe.loc[ticker]
        try:
            df = fdr.DataReader(ticker, start, end)
        except Exception:
            return None
        return evaluate_stock(ticker, row["종목명"], row["시장구분"], row["시가총액"], df)

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, ticker): ticker for ticker in universe.index}
        for future in as_completed(futures):
            done += 1
            row = future.result()
            if row is not None:
                results.append(row)
            if done % 200 == 0 or done == total:
                log(f"  진행: {done}/{total}")

    log(f"✔ {len(results)}개 종목 평가 완료 (데이터 부족 등으로 {total - len(results)}개 제외)")
    return results


def save_chart_screener_cache(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "generated_at": pd.Timestamp.today().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "rows": rows,
    }
    with open(CHART_SCREENER_CACHE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_chart_screener_cache():
    """캐시 JSON을 로드한다. 없거나 손상되었으면 None."""
    if not os.path.exists(CHART_SCREENER_CACHE):
        return None
    try:
        with open(CHART_SCREENER_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

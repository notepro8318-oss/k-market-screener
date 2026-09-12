"""
"차트 분석 종목 찾기" - 월봉 추세(5개월 이동평균선)와 거래량·거래대금 급증을 함께 보는
차트 기반 종목 발굴 모듈.

DART 재무데이터가 필요 없어 OpenDART의 해외 IP 차단 문제는 없지만, 코스피·코스닥 전 종목
(약 2,500개)의 일봉 데이터를 매번 새로 받아야 해서 Streamlit 요청 중에 실시간으로 돌리기엔
너무 느리고 불안정하다(수분 이상 소요, 아웃바운드 연결 다수). 그래서 VKOSPI/코스피 PBR/
산업군 MDD와 동일한 패턴으로 로컬 배치(crawl_chart_screener.py)에서 하루 한 번 전종목을
수집·평가해 data/chart_screener_cache.json에 저장하고, Streamlit 앱은 그 캐시만 읽는다.
"급증" 신호는 그날그날 새로 나오는 것이라 매일 갱신이 특히 중요하다 (Windows 작업 스케줄러에
다른 시장 지표 크롤러와 함께 등록됨).

캐시에는 최종 통과 여부가 아니라 종목별 계산된 지표(5개월선 연속 상회 개월수, 거래량/거래대금
배수, 3개월 저점 대비 상승률, 당일 거래대금)만 저장한다. 통과 기준(최근 3개월 연속·3배 이상 등)은
화면(views/chart_screener_view.py)에서 슬라이더로 조절 가능해야 하므로, 배치 단계에서 미리
걸러버리지 않고 원본 지표를 그대로 남겨둔다.
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

# 월봉 5개월선 연속 상회 판정에 필요한 최소 개월 수(5개월선 계산에 5개월 + 여유분) +
# 20일 평균 거래량/거래대금 계산 여유를 확보하기 위해 넉넉히 9개월치 일봉을 받는다.
_HISTORY_MONTHS = 9


def fetch_universe():
    """
    코스피·코스닥 상장 전 종목(종목코드/종목명/시장구분)을 가져온다.
    screener.run_first_stage_screening과 동일하게 KOSDAQ GLOBAL은 KOSDAQ으로 합치고
    KONEX는 이 스크리너의 대상이 아니므로 제외한다.
    """
    df_listing = fdr.StockListing("KRX")
    df_listing = df_listing.set_index("Code")[["Name", "Market"]]
    df_listing.columns = ["종목명", "시장구분"]
    df_listing["시장구분"] = df_listing["시장구분"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    return df_listing[df_listing["시장구분"].isin(["KOSPI", "KOSDAQ"])]


def evaluate_stock(ticker, name, market, df):
    """
    종목 하나의 일봉 OHLCV(df, DatetimeIndex + Close/Volume 컬럼)로부터
    - 월봉 종가가 5개월 이동평균선 위에서 연속으로 머문 개월 수(가장 최근 달부터 역순)
    - 최근 거래일 거래량/거래대금이 직전 20거래일 평균 대비 몇 배인지
    - 최근 3개월 저점 대비 현재가 상승률(%)
    - 최근 거래일 거래대금(원)
    을 계산해 반환한다. 판정에 필요한 데이터가 부족하면 None.
    """
    if df is None or df.empty or "Close" not in df.columns or "Volume" not in df.columns:
        return None

    df = df.sort_index()
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    trading_value = close * volume

    monthly_close = close.resample("ME").last().dropna()
    if len(monthly_close) < 6:  # 5개월선 계산(5개) + 최소 1개월 판정
        return None
    ma5 = monthly_close.rolling(5).mean()

    streak = 0
    for m_close, m_ma5 in zip(monthly_close.iloc[::-1], ma5.iloc[::-1]):
        if pd.isna(m_ma5):
            break
        if m_close > m_ma5:
            streak += 1
        else:
            break

    if len(close) < 21:
        return None

    latest_volume = volume.iloc[-1]
    latest_value = trading_value.iloc[-1]
    prior_volume_avg20 = volume.iloc[-21:-1].mean()
    prior_value_avg20 = trading_value.iloc[-21:-1].mean()
    volume_ratio = (latest_volume / prior_volume_avg20) if prior_volume_avg20 > 0 else None
    value_ratio = (latest_value / prior_value_avg20) if prior_value_avg20 > 0 else None

    latest_date = close.index[-1]
    recent_close = close[close.index >= latest_date - pd.DateOffset(months=3)]
    three_month_low = recent_close.min() if not recent_close.empty else None
    latest_close = close.iloc[-1]
    rise_from_low_pct = (
        (latest_close / three_month_low - 1) * 100
        if three_month_low and three_month_low > 0 else None
    )

    return {
        "종목코드": ticker,
        "종목명": name,
        "시장구분": market,
        "5개월선_연속상회월수": streak,
        "거래량_배수": round(volume_ratio, 2) if volume_ratio is not None else None,
        "거래대금_배수": round(value_ratio, 2) if value_ratio is not None else None,
        "당일_거래대금": round(latest_value) if pd.notna(latest_value) else None,
        "3개월저점대비_상승률(%)": round(rise_from_low_pct, 2) if rise_from_low_pct is not None else None,
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
        return evaluate_stock(ticker, row["종목명"], row["시장구분"], df)

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

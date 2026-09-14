"""
"차트 분석 종목 찾기" - 첨부된 5단계 시스템 트레이딩 전략 중 1~3단계(장기 추세 필터 ->
수급 유효성 검증 -> 상투 분산 배제)를 구현한 스크리닝 모듈.

4단계(2차 파동 타점: 1차 돌파 후 눌림목 대기 -> 재돌파 확인)는 "오늘 하루 스냅샷"이 아니라
시간 흐름을 추적해야 하는 상태 기반 로직이라 별도 설계가 필요해 이번 구현에서는 제외했다
(사용자와 논의 후 보류 결정). 5단계(청산 프로토콜: 손절/익절/추세청산)는 실제 보유 종목이
있어야 의미가 있는 포지션 관리 로직이라 코드가 아닌 화면 설명 텍스트로만 안내한다
(views/chart_screener_view.py 참고).

DART 재무데이터가 필요 없어 OpenDART의 해외 IP 차단 문제는 없지만, 코스피·코스닥 전 종목
(약 2,500개)의 일봉 데이터를 매번 새로 받아야 해서 Streamlit 요청 중에 실시간으로 돌리기엔
너무 느리고 불안정하다(수분 이상 소요, 아웃바운드 연결 다수). 그래서 VKOSPI/코스피 PBR/
산업군 MDD와 동일한 패턴으로 로컬 배치(crawl_chart_screener.py)에서 하루 한 번 전종목을
수집·평가해 data/chart_screener_cache.json에 저장하고, Streamlit 앱은 그 캐시만 읽는다.
"급증" 신호는 그날그날 새로 나오는 것이라 매일 갱신이 특히 중요하다 (Windows 작업 스케줄러에
다른 시장 지표 크롤러와 함께 등록됨).

캐시에는 최종 통과 여부가 아니라 종목별 계산된 원본 지표만 저장한다. 통과 기준(연속 개월 수,
배수, 이격도 상한 등)은 화면에서 슬라이더로 조절 가능해야 하므로, 배치 단계에서 미리 걸러버리지
않는다.

거래대금 최소 기준은 시가총액 규모별로 절대금액을 달리 적용한다(대형주는 원래도 거래대금이
커서 상대 배수로는 잘 안 걸리고, 소형주는 반대로 배수는 쉽게 튀지만 절대금액이 작아 의미 있는
자금 유입인지 판별이 안 되는 문제가 있었다).
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

# 월봉 5개월선 연속 상회 판정(5개월선 계산에 5개 + 여유분)과 일봉 60일 이동평균 계산에
# 필요한 만큼 넉넉히 24개월(2년)치 일봉을 받는다. NaverDailyReader는 요청 기간과 무관하게
# 매번 최대 6000일치를 내려받은 뒤 클라이언트에서 잘라내는 구조라, 기간을 늘려도 크롤링
# 속도에는 영향이 없다.
_HISTORY_MONTHS = 24

# 시가총액 1조원을 대형주/소형주 경계로 삼는다(화면에서 조절 가능).
LARGE_CAP_THRESHOLD = 1_000_000_000_000

# 캔들 몸통이 0(시가=종가, 도지)인데 윗꼬리가 있는 경우, 몸통 대비 배수를 정의할 수 없어
# "명백히 몸통을 초과"하는 것으로 간주해 큰 값(200%)을 대입한다.
_DOJI_WITH_WICK_SENTINEL_PCT = 200.0


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
    종목 하나의 일봉 OHLCV(df, DatetimeIndex + Open/High/Low/Close/Volume 컬럼)로부터
    1단계(장기 추세): 월봉 종가가 5개월 이동평균선 위에서 연속으로 머문 개월 수
    2단계(수급 유효성): 최근 거래일 거래량이 직전 20거래일 평균 대비 배수, 최근 거래일 거래대금
    3단계(상투 분산 배제): 최근 6개월 저점 대비 상승률, 월봉 5MA/일봉 60MA 이격도(%),
                        대량거래일(최근 거래일) 캔들의 윗꼬리/몸통 비율(%)
    을 계산해 반환한다. 판정에 필요한 데이터가 부족하면 None.
    """
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    if df is None or df.empty or not required_cols.issubset(df.columns):
        return None

    df = df.sort_index()
    open_ = df["Open"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
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

    if len(close) < 60:  # 일봉 60MA 계산에 필요
        return None

    # --- 2단계: 최근 거래일(=대량거래일 후보) 거래량 배수·거래대금 ---
    latest_volume = volume.iloc[-1]
    latest_value = trading_value.iloc[-1]
    prior_volume_avg20d = volume.iloc[-21:-1].mean()
    daily_volume_ratio = (latest_volume / prior_volume_avg20d) if prior_volume_avg20d > 0 else None

    # --- 3단계: 6개월 저점 대비 상승률 ---
    latest_date = close.index[-1]
    recent_close = close[close.index >= latest_date - pd.DateOffset(months=6)]
    six_month_low = recent_close.min() if not recent_close.empty else None
    latest_close = close.iloc[-1]
    rise_from_low_pct = (
        (latest_close / six_month_low - 1) * 100
        if six_month_low and six_month_low > 0 else None
    )

    # --- 3단계: 이동평균 이격도(%) = 종가 / 이동평균 * 100 ---
    latest_ma5_monthly = ma5_monthly.iloc[-1]
    monthly_ma5_disparity_pct = (
        (monthly_close.iloc[-1] / latest_ma5_monthly * 100) if pd.notna(latest_ma5_monthly) and latest_ma5_monthly > 0 else None
    )
    ma60_daily = close.rolling(60).mean()
    latest_ma60_daily = ma60_daily.iloc[-1]
    daily_ma60_disparity_pct = (
        (latest_close / latest_ma60_daily * 100) if pd.notna(latest_ma60_daily) and latest_ma60_daily > 0 else None
    )

    # --- 3단계: 대량거래일(최근 거래일) 캔들 윗꼬리/몸통 비율(%) ---
    o, h, l, c = open_.iloc[-1], high.iloc[-1], low.iloc[-1], close.iloc[-1]
    body = abs(c - o)
    upper_wick = h - max(o, c)
    if body > 0:
        upper_wick_body_ratio_pct = upper_wick / body * 100
    else:
        upper_wick_body_ratio_pct = _DOJI_WITH_WICK_SENTINEL_PCT if upper_wick > 0 else 0.0

    marcap = float(marcap) if pd.notna(marcap) else None

    return {
        "종목코드": ticker,
        "종목명": name,
        "시장구분": market,
        "시가총액": round(marcap) if marcap is not None else None,
        "시총구분": ("대형주" if marcap is not None and marcap >= LARGE_CAP_THRESHOLD else "소형주"),
        "5개월선_연속상회월수": streak,
        "일간_거래량배수": round(daily_volume_ratio, 2) if daily_volume_ratio is not None else None,
        "당일_거래대금": round(latest_value) if pd.notna(latest_value) else None,
        "6개월저점대비_상승률(%)": round(rise_from_low_pct, 2) if rise_from_low_pct is not None else None,
        "월봉5MA이격도(%)": round(monthly_ma5_disparity_pct, 2) if monthly_ma5_disparity_pct is not None else None,
        "일봉60MA이격도(%)": round(daily_ma60_disparity_pct, 2) if daily_ma60_disparity_pct is not None else None,
        "윗꼬리몸통비율(%)": round(upper_wick_body_ratio_pct, 2),
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

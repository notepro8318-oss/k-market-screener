"""
코스피/코스닥 "시장 진입 타이밍" 대시보드용 데이터 수집·계산.

종목 스크리닝(screener.py)과 달리 OpenDART에 의존하지 않는다 - 지수/환율/VIX는
FinanceDataReader(Naver·Yahoo 등 공개 소스 기반)로, CNN Fear & Greed는 비공식 공개
API로 가져온다. 이 소스들은 해외 IP 차단이 없어 배포 환경(Streamlit Cloud)에서도
매번 라이브로 조회한다.

VKOSPI(코스피 변동성지수)·코스피 PBR 둘은 예외다 - 각각 investing.com/indexergo.com을
크롤링해야 하는데(전자는 Cloudflare 봇 차단까지 걸려 있음), DART와 같은 방식으로 로컬에서
crawl_vkospi.py·crawl_kospi_pbr.py를 미리 실행해 data/vkospi_cache.json·
data/kospi_pbr_cache.json에 저장해두고 여기서는 그 캐시만 읽는다. 캐시가 오래됐거나
없으면 VKOSPI는 사용자가 제시한 조건에 이미 명시된 대체 지표인 VIX로, 코스피 PBR은
사용자 직접 입력으로 자동 전환한다.

7개 지표(크롤링 캐시가 없을 때만 코스피 PBR을 사용자가 직접 입력) 중 충족 개수에 따라:
  - 4개 이상(PBR 충족 포함): 적극 진입
  - 3개 이상: 정상 진입
  - 2개 이하: 진입 보류
"""

import json
import re

import numpy as np
import pandas as pd
import requests
import FinanceDataReader as fdr

from screener import DATA_DIR

_DEFAULT_TIMEOUT = 15
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
VKOSPI_CACHE = DATA_DIR / "vkospi_cache.json"
VKOSPI_URL = "https://kr.investing.com/indices/kospi-volatility"
KOSPI_PBR_CACHE = DATA_DIR / "kospi_pbr_cache.json"
KOSPI_PBR_URL = "https://www.indexergo.com/series/?detailId=20206&frq=D"


def fetch_index_history(symbol, years=2):
    """
    KS11(코스피)/KQ11(코스닥)/USD-KRW/VIX 등 일별 OHLC를 가져온다. 실패하면 None.
    간혹 최신 1~2개 행의 Close가 NaN으로 들어오는 경우(당일 시세 미확정 등)가 있어
    Close 기준으로 결측 행을 제거한다 - 안 하면 rolling 계산(볼린저 밴드 등)이 전부 NaN이 된다.
    """
    try:
        start = pd.Timestamp.today() - pd.DateOffset(years=years)
        df = fdr.DataReader(symbol, start)
        if df is None or df.empty:
            return None
        df = df.dropna(subset=["Close"])
        return df if not df.empty else None
    except Exception:
        return None


def fetch_cnn_fear_greed():
    """
    CNN Fear & Greed Index 현재 점수(0~100, 낮을수록 공포). 비공식 API라 CNN 사이트에서
    직접 여는 것처럼 Referer/Origin 헤더를 붙여야 봇 차단(HTTP 418)을 피할 수 있다.
    실패하면 (None, None)을 반환 - 판정에서 제외(➖) 처리하는 데 사용.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://edition.cnn.com/markets/fear-and-greed",
        "Origin": "https://edition.cnn.com",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=headers, timeout=_DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()["fear_and_greed"]
        return float(data["score"]), data["rating"]
    except Exception:
        return None, None


def fetch_vkospi_investing():
    """
    investing.com VKOSPI(코스피 변동성지수) 페이지에서 현재가를 가져온다. 이 사이트는
    Cloudflare 봇 차단이 걸려 있어 일반 requests로는 403이 나고, TLS 지문을 크롬처럼
    위장하는 curl_cffi(impersonate="chrome")를 써야 통과한다(라이브로 직접 확인함).
    그래도 언제 막힐지 모르는 비공식 스크래핑이라 실패하면 None을 반환한다 -
    crawl_vkospi.py에서만 호출하고, 실패해도 기존 캐시를 건드리지 않는다.
    """
    from curl_cffi import requests as creq
    try:
        r = creq.get(VKOSPI_URL, impersonate="chrome", timeout=20)
        r.raise_for_status()
        m = re.search(r'data-test="instrument-price-last">([\d.,]+)<', r.text)
        if not m:
            return None
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def fetch_kospi_pbr_indexergo():
    """
    indexergo.com에서 코스피(전체지수, KOSPI200 아님) PBR 확정치를 가져온다. 이 사이트는
    Cloudflare 차단이 없어 일반 requests로 충분하다(라이브로 직접 확인함). 페이지에
    "YYYY.MM.DD 마감 기준 PBR: N.NN" 형식으로 박혀 있어 정규식으로 바로 뽑는다.
    실패하면 (None, None) - crawl_kospi_pbr.py에서만 호출한다.
    """
    try:
        r = requests.get(KOSPI_PBR_URL, headers={"User-Agent": _UA}, timeout=_DEFAULT_TIMEOUT)
        r.raise_for_status()
        m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s*마감 기준 PBR:\s*(\d+\.\d+)", r.text)
        if not m:
            return None, None
        y, mo, d, val = m.groups()
        return float(val), f"{y}-{mo}-{d}"
    except Exception:
        return None, None


def _save_json_cache(path, value, as_of_date=None):
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {"value": value, "date": as_of_date or pd.Timestamp.today().strftime("%Y-%m-%d")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _load_json_cache(path, max_age_days=5):
    """
    캐시가 없거나 max_age_days보다 오래됐으면 (value=None, 캐시일자)를 반환해 호출부가
    대체 지표/수동 입력으로 폴백하도록 한다. 크롤러를 며칠 못 돌려도 대시보드가 죽지 않는다.
    """
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_date = pd.Timestamp(data["date"])
        age_days = (pd.Timestamp.today().normalize() - cached_date).days
        date_str = cached_date.strftime("%Y-%m-%d")
        if age_days > max_age_days:
            return None, date_str
        return data["value"], date_str
    except Exception:
        return None, None


def save_vkospi_cache(value):
    _save_json_cache(VKOSPI_CACHE, value)


def load_vkospi_cache(max_age_days=5):
    return _load_json_cache(VKOSPI_CACHE, max_age_days)


def save_kospi_pbr_cache(value, as_of_date):
    _save_json_cache(KOSPI_PBR_CACHE, value, as_of_date)


def load_kospi_pbr_cache(max_age_days=5):
    return _load_json_cache(KOSPI_PBR_CACHE, max_age_days)


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_bollinger_breakout(df, window=20, num_std=2, min_daily_range=15):
    """
    원/달러 환율: 일봉 볼린저 밴드(20,2) 상단 돌파 + 당일 변동폭(고가-저가) 15원 이상.
    둘 다 만족해야 "환차손 회피 패닉 셀링" 신호로 본다.
    """
    close = df["Close"]
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = (ma + num_std * std).iloc[-1]
    last_close = close.iloc[-1]
    daily_range = df["High"].iloc[-1] - df["Low"].iloc[-1]
    met = bool(last_close > upper and daily_range >= min_daily_range)
    return met, {"종가": round(last_close, 2), "상단밴드": round(upper, 2), "당일변동폭": round(daily_range, 2)}


def compute_mdd(df, window_days=252, threshold_pct=15):
    """52주(약 252거래일) 고점 대비 낙폭(MDD, %). threshold_pct 이상 하락했으면 충족."""
    close = df["Close"]
    rolling_high = close.rolling(window_days, min_periods=20).max()
    mdd = (close.iloc[-1] / rolling_high.iloc[-1] - 1) * 100
    met = bool(mdd <= -threshold_pct)
    return met, round(mdd, 2)


def compute_disparity_200(df, threshold_pct=88):
    """현재가 ÷ 200일 이동평균 × 100 (이격도, %). threshold_pct 이하면 과매도 충족."""
    close = df["Close"]
    ma200 = close.rolling(200).mean()
    if pd.isna(ma200.iloc[-1]):
        return None, None
    disparity = close.iloc[-1] / ma200.iloc[-1] * 100
    met = bool(disparity <= threshold_pct)
    return met, round(disparity, 2)


def compute_rsi_condition(df, daily_threshold=30, weekly_threshold=35, period=14):
    """일봉 RSI(14) daily_threshold 이하 또는 주봉 RSI(14) weekly_threshold 이하면 충족."""
    close = df["Close"]
    daily_rsi = _rsi(close, period).iloc[-1]
    weekly_close = close.resample("W-FRI").last().dropna()
    weekly_rsi = _rsi(weekly_close, period).iloc[-1] if len(weekly_close) > period else np.nan

    daily_ok = pd.notna(daily_rsi) and daily_rsi <= daily_threshold
    weekly_ok = pd.notna(weekly_rsi) and weekly_rsi <= weekly_threshold
    if pd.isna(daily_rsi) and pd.isna(weekly_rsi):
        return None, {"일봉RSI": None, "주봉RSI": None}
    met = bool(daily_ok or weekly_ok)
    return met, {
        "일봉RSI": round(daily_rsi, 1) if pd.notna(daily_rsi) else None,
        "주봉RSI": round(weekly_rsi, 1) if pd.notna(weekly_rsi) else None,
    }


def _fill_pct(actual, threshold, direction="ge"):
    """
    카드 게이지용 0~100 충족도(%). direction="ge"면 값이 클수록 충족에 가깝고
    (actual/threshold*100), "le"면 작을수록 충족에 가깝다(threshold/actual*100).
    실제 충족 여부(True/False/None) 판정과는 별개로 순수 시각화용 근사치다.
    """
    if actual is None or not threshold:
        return None
    pct = (actual / threshold * 100) if direction == "ge" else (threshold / actual * 100 if actual > 0 else 100)
    return max(0, min(100, round(pct)))


def build_dashboard(pbr_override=None):
    """
    7개 지표를 계산해 결과 리스트(각 항목: 구분/지표/값/기준/충족여부/설명)와 요약(충족 개수,
    진입 판정)을 반환한다. 데이터를 못 가져온 지표는 충족여부 None(➖)으로 판정에서 제외한다.

    pbr_override가 주어지면(사용자가 직접 입력) 크롤링 캐시보다 그 값을 우선한다 -
    크롤링 캐시가 없거나 오래됐을 때의 수동 보정용.
    """
    kospi = fetch_index_history("KS11")
    kosdaq = fetch_index_history("KQ11")
    usdkrw = fetch_index_history("USD/KRW")
    vix = fetch_index_history("VIX", years=1)
    fg_score, fg_rating = fetch_cnn_fear_greed()

    rows = []

    # 1. 밸류에이션(절대 저평가) - indexergo.com 크롤링 캐시 우선, 없으면 수동 입력값 사용
    pbr_cached, pbr_date = load_kospi_pbr_cache()
    if pbr_override is not None:
        pbr_value, pbr_source = pbr_override, "직접 입력값 사용"
    elif pbr_cached is not None:
        pbr_value, pbr_source = pbr_cached, f"indexergo.com 크롤링 캐시 기준일: {pbr_date} (crawl_kospi_pbr.py로 갱신)"
    else:
        pbr_value = None
        pbr_source = (
            f"크롤링 캐시 오래됨(마지막 {pbr_date}) - 직접 입력 필요" if pbr_date
            else "크롤링 캐시 없음 - 직접 입력 필요"
        )
    pbr_met = None if pbr_value is None else pbr_value <= 0.85
    rows.append({
        "구분": "밸류에이션", "지표": "코스피 PBR", "값": pbr_value, "단위": "배",
        "기준": "0.85배 이하", "충족": pbr_met, "설명": pbr_source,
        "fill_pct": _fill_pct(pbr_value, 0.85, "le"),
    })

    # 2. 변동성/심리 - VKOSPI(크롤링 캐시) 우선, 캐시가 없거나 오래됐으면 VIX로 자동 전환
    vkospi_value, vkospi_date = load_vkospi_cache()
    if vkospi_value is not None:
        rows.append({
            "구분": "변동성·심리", "지표": "VKOSPI", "값": vkospi_value, "단위": "",
            "기준": "25 이상", "충족": vkospi_value >= 25,
            "설명": f"investing.com 크롤링 캐시 기준일: {vkospi_date} (crawl_vkospi.py로 갱신)",
            "fill_pct": _fill_pct(vkospi_value, 25, "ge"),
        })
    else:
        if vix is not None:
            vix_last = round(vix["Close"].iloc[-1], 2)
            vix_met = vix_last >= 30
        else:
            vix_last, vix_met = None, None
        stale_note = f" (마지막 캐시일 {vkospi_date}, 5일 초과해 미사용)" if vkospi_date else " (크롤링 캐시 없음)"
        rows.append({
            "구분": "변동성·심리", "지표": "VIX (VKOSPI 대체)", "값": vix_last, "단위": "",
            "기준": "30 이상", "충족": vix_met,
            "설명": "VKOSPI 캐시가 없거나 오래돼 조건에 함께 명시된 VIX로 대체" + stale_note,
            "fill_pct": _fill_pct(vix_last, 30, "ge"),
        })

    # 3. 변동성/심리 - CNN Fear & Greed
    fg_met = None if fg_score is None else fg_score <= 20
    rows.append({
        "구분": "변동성·심리", "지표": "CNN Fear & Greed", "값": round(fg_score, 1) if fg_score else None,
        "단위": "점", "기준": "20 이하 (Extreme Fear)", "충족": fg_met,
        "설명": f"등급: {fg_rating}" if fg_rating else "비공식 API 응답 실패",
        "fill_pct": _fill_pct(fg_score, 20, "le"),
    })

    # 4. 외환 스트레스
    if usdkrw is not None and len(usdkrw) >= 20:
        fx_met, fx_detail = compute_bollinger_breakout(usdkrw)
        fx_val = f"{fx_detail['종가']}원 (상단 {fx_detail['상단밴드']}원, 변동폭 {fx_detail['당일변동폭']}원)"
        fx_fill = _fill_pct(fx_detail["종가"], fx_detail["상단밴드"], "ge")
    else:
        fx_met, fx_val, fx_fill = None, None, None
    rows.append({
        "구분": "외환 스트레스", "지표": "원/달러 환율", "값": fx_val, "단위": "",
        "기준": "볼린저(20,2) 상단 돌파 + 일간 변동폭 15원 이상", "충족": fx_met,
        "설명": "외국인 무차별 패닉 셀링(환차손 회피) 정점 포착", "fill_pct": fx_fill,
    })

    # 5. 지수 낙폭(MDD) - 코스피 -15% 또는 코스닥 -20% (둘 중 하나만 충족해도 인정)
    mdd_met, mdd_detail = None, {}
    if kospi is not None and len(kospi) >= 20:
        kospi_mdd_met, kospi_mdd = compute_mdd(kospi, threshold_pct=15)
        mdd_detail["코스피"] = kospi_mdd
    else:
        kospi_mdd_met, mdd_detail["코스피"] = None, None
    if kosdaq is not None and len(kosdaq) >= 20:
        kosdaq_mdd_met, kosdaq_mdd = compute_mdd(kosdaq, threshold_pct=20)
        mdd_detail["코스닥"] = kosdaq_mdd
    else:
        kosdaq_mdd_met, mdd_detail["코스닥"] = None, None
    if kospi_mdd_met is not None or kosdaq_mdd_met is not None:
        mdd_met = bool(kospi_mdd_met) or bool(kosdaq_mdd_met)
    kospi_mdd = mdd_detail.get("코스피")
    kosdaq_mdd = mdd_detail.get("코스닥")
    mdd_fill_candidates = [
        f for f in [
            _fill_pct(abs(kospi_mdd) if kospi_mdd is not None else None, 15, "ge"),
            _fill_pct(abs(kosdaq_mdd) if kosdaq_mdd is not None else None, 20, "ge"),
        ] if f is not None
    ]
    rows.append({
        "구분": "지수 낙폭·이격", "지표": "MDD(52주 고점 대비)",
        "값": f"코스피 {kospi_mdd}% / 코스닥 {kosdaq_mdd}%",
        "단위": "", "기준": "코스피 -15% 이상 또는 코스닥 -20% 이상", "충족": mdd_met,
        "설명": "52주 고점 대비 유의미한 시스템적 가격 조정 확인 (둘 중 하나만 충족해도 인정)",
        "fill_pct": max(mdd_fill_candidates) if mdd_fill_candidates else None,
    })

    # 6. 200일선 이격도 (코스피 기준)
    if kospi is not None and len(kospi) >= 200:
        disparity_met, disparity_val = compute_disparity_200(kospi)
    else:
        disparity_met, disparity_val = None, None
    rows.append({
        "구분": "지수 낙폭·이격", "지표": "200일선 이격도(코스피)", "값": disparity_val, "단위": "%",
        "기준": "88% 이하", "충족": disparity_met,
        "설명": "단순 하향 이탈이 아닌, 평균 대비 12% 이상 폭락한 극단적 과매도 상태",
        "fill_pct": _fill_pct(disparity_val, 88, "le"),
    })

    # 7. RSI(14) (코스피 기준)
    if kospi is not None and len(kospi) >= 100:
        rsi_met, rsi_detail = compute_rsi_condition(kospi)
    else:
        rsi_met, rsi_detail = None, {"일봉RSI": None, "주봉RSI": None}
    rsi_fill_candidates = [
        f for f in [
            _fill_pct(rsi_detail.get("일봉RSI"), 30, "le"),
            _fill_pct(rsi_detail.get("주봉RSI"), 35, "le"),
        ] if f is not None
    ]
    rows.append({
        "구분": "지수 낙폭·이격", "지표": "RSI(14, 코스피)",
        "값": f"일봉 {rsi_detail.get('일봉RSI')} / 주봉 {rsi_detail.get('주봉RSI')}",
        "단위": "", "기준": "주봉 35 이하 또는 일봉 30 이하", "충족": rsi_met,
        "설명": "일시적 눌림목이 아닌 중장기 추세 과매도 구간 검증",
        "fill_pct": max(rsi_fill_candidates) if rsi_fill_candidates else None,
    })

    met_count = sum(1 for r in rows if r["충족"] is True)
    unknown_count = sum(1 for r in rows if r["충족"] is None)

    if met_count >= 4 and pbr_met:
        verdict = "적극 진입"
    elif met_count >= 3:
        verdict = "정상 진입"
    else:
        verdict = "진입 보류"

    summary = {
        "충족_개수": met_count, "전체_지표수": len(rows),
        "데이터없음_개수": unknown_count, "판정": verdict,
    }
    return rows, summary

"""
WICS(WISE Index) 27개 산업군(중분류)의 YTD 최대낙폭(MDD)을 계산하기 위한 데이터 수집·캐시 모듈.

wiseindex.com(FnGuide가 운영하는 WICS 공식 산출기관)의 비공식이지만 공개된 JSON
엔드포인트(GetIndexComponets)를 사용한다. 이 엔드포인트는 특정 일자의 섹터 구성종목과
섹터 전체 시가총액(MKT_VAL)을 반환하는데, WICS 지수 자체가 유동시가총액가중 방식이라
MKT_VAL 시계열은 실제 지수 값과 비례 관계에 있어 MDD(등락률 기반 지표) 계산에는 그대로
대체 가능하다.

KRX 로그인이 필요한 pykrx, 업종 지수를 지원하지 않는 FinanceDataReader와 달리 이 API는
로그인 없이 바로 호출 가능함을 확인했다(단, 하루치 조회당 요청 1건이 필요해 1년치 백필은
로컬 배치 크롤러로 한 번만 수행하고, 이후에는 최신 1일치만 증분 수집한다 - VKOSPI/코스피
PBR 크롤러와 동일한 패턴).
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

_DEFAULT_TIMEOUT = 15
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_WISE_URL = "https://www.wiseindex.com/Index/GetIndexComponets"

# WICS 산업군(Level 2, 중분류) 27개 - wiseindex.com에 G(대분류2자리)+(산업군2자리) 코드로
# 하나씩 직접 조회해 실제 응답이 오는 코드만 골라 확인했다(GICS 산업군 개수와도 대조 완료).
SECTOR_CODES = {
    "G1010": "에너지",
    "G1510": "소재",
    "G2010": "자본재",
    "G2020": "상업서비스와공급품",
    "G2030": "운송",
    "G2510": "자동차와부품",
    "G2520": "내구소비재와의류",
    "G2530": "호텔,레스토랑,레저등",
    "G2550": "소매(유통)",
    "G2560": "교육서비스",
    "G3010": "식품과기본식료품소매",
    "G3020": "식품,음료,담배",
    "G3030": "가정용품과개인용품",
    "G3510": "건강관리장비와서비스",
    "G3520": "제약과생물공학",
    "G4010": "은행",
    "G4020": "증권",
    "G4030": "다각화된금융",
    "G4040": "보험",
    "G4050": "부동산",
    "G4510": "소프트웨어와서비스",
    "G4520": "기술하드웨어와장비",
    "G4530": "반도체와반도체장비",
    "G4540": "디스플레이",
    "G5010": "전기통신서비스",
    "G5020": "미디어와엔터테인먼트",
    "G5510": "유틸리티",
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SECTOR_MDD_CACHE = os.path.join(DATA_DIR, "sector_mdd_cache.json")


def fetch_sector_mkt_val(sec_cd, date_str):
    """단일 (섹터, 일자)의 섹터 전체 시가총액을 조회한다. 휴장일/데이터없음이면 None."""
    try:
        r = requests.get(
            _WISE_URL, params={"ceil_yn": "0", "dt": date_str, "sec_cd": sec_cd},
            timeout=_DEFAULT_TIMEOUT, headers={"User-Agent": _UA},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("list"):
            return None
        return float(data["info"]["MKT_VAL"])
    except Exception:
        return None


def crawl_sector_series(sec_cd, dates, max_workers=6):
    """
    주어진 날짜 목록(YYYYMMDD 문자열)에 대해 병렬로 시가총액을 조회해
    {date: mkt_val} dict를 반환한다(휴장일은 제외).
    """
    result = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_sector_mkt_val, sec_cd, d): d for d in dates}
        for future in as_completed(futures):
            d = futures[future]
            val = future.result()
            if val is not None:
                result[d] = val
    return result


def compute_mdd_from_series(date_to_value):
    """
    {date: value} dict(날짜 오름차순 정렬 불필요)로부터 YTD 최대낙폭(%)과
    고점/저점 일자를 계산한다. 데이터가 2개 미만이면 None.
    """
    if not date_to_value or len(date_to_value) < 2:
        return None
    series = pd.Series(date_to_value).sort_index()
    running_max = series.cummax()
    drawdown = series / running_max - 1
    trough_date = drawdown.idxmin()
    mdd_pct = round(float(drawdown.loc[trough_date]) * 100, 2)
    peak_date = series.loc[:trough_date].idxmax()
    return {
        "mdd_pct": mdd_pct, "peak_date": peak_date, "trough_date": trough_date,
        "latest_date": series.index[-1], "latest_value": float(series.iloc[-1]),
    }


def load_sector_cache():
    """캐시 JSON을 로드한다. {sec_cd: {date: mkt_val}} 형태, 없으면 빈 dict."""
    if not os.path.exists(SECTOR_MDD_CACHE):
        return {}
    try:
        with open(SECTOR_MDD_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_sector_cache(cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SECTOR_MDD_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def build_sector_mdd_table():
    """
    캐시된 {sec_cd: {date: mkt_val}}로부터 산업군별 YTD MDD 표(list of dict)를 만든다.
    화면 표시용 - 크롤링은 하지 않고 이미 저장된 캐시만 읽는다.
    """
    cache = load_sector_cache()
    rows = []
    for sec_cd, name in SECTOR_CODES.items():
        series = cache.get(sec_cd, {})
        stats = compute_mdd_from_series(series)
        rows.append({
            "산업군": name,
            "YTD MDD(%)": stats["mdd_pct"] if stats else None,
            "고점": stats["peak_date"] if stats else None,
            "저점": stats["trough_date"] if stats else None,
            "기준일": stats["latest_date"] if stats else None,
        })
    return rows

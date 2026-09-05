"""
"한국 경제사이클" 4-Layer 대시보드용 데이터 수집·계산 (Fidelity 스타일 business-cycle
waterfall 참고: 글로벌 수요 -> 한국 수출 -> 국내 공장 -> 금융/환율 순서로 신호가
전달되는지 확인).

원래 설계(사용자 제시)는 8개 지표였으나, 조사 결과 아래 2개는 무료 공식 API가 없어
대체지표로 교체했다:
  - 미국 ISM 신규주문-재고 스프레드 -> FRED가 2016년에 ISM 데이터를 완전히 제거해서
    (라이선스 분쟁) 무료 소스가 없음. 대신 미국 내구재 신규수주(DGORDER) YoY - 제조업
    재고(MNFCTRIMSA) YoY 스프레드로 대체 - "주문 모멘텀 대비 재고 축적 속도"라는 같은
    개념을 서베이 대신 실물 하드데이터로 근사한다.
  - 중국 차이신 제조업 PMI -> 마찬가지로 무료 API 없음(유료 라이선스). 대신 중국
    국가통계국(NBS) 공식 제조업 PMI로 대체 - 다만 국유기업 비중이 높아 차이신
    (민간·중소기업 위주 서베이)과는 표본 성격이 다르다는 점을 감안해야 한다.

한국 데이터는 전부 한국은행 ECOS(기존 스크리너에서 쓰는 키 재사용, 신규 등록 불필요)로
커버된다:
  - 수출: 901Y118(수출금액, 월별) - "비반도체 수출" 단독 통계(MTI/HS 품목별 세부 분류)는
    ECOS에 없어(관세청/KOSIS 별도 키가 필요) 총 수출 YoY로 단순화했다(반도체 착시를
    걸러내는 원래 취지는 이번 버전에서 빠짐).
  - 경기: 901Y067의 I16E(선행지수순환변동치) + 901Y032(산업별 생산/출하/재고 지수)의
    총지수(I11A) 출하지수(구분 3)/재고지수(구분 5)로 계산한 재고순환지표(출하 YoY -
    재고 YoY, 통계청 공식 정의와 동일) - 둘 다 기존 ECOS 키로 바로 커버된다.
  - 금리: 817Y002의 국고채 10년(010210000) - 3년(010200000) 스프레드.

원/달러 60일 이평선은 market_dashboard.py의 fetch_index_history()를 그대로 재사용한다.
"""

import requests
import pandas as pd
import FinanceDataReader as fdr

_DEFAULT_TIMEOUT = 20
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CHINA_PMI_URL = "https://chinadata.live/api/v2/data/china-pmi"


def _yoy(series):
    """월별 시계열(오래된->최신)의 최신값 YoY(%). 13개월 미만이면 None."""
    if series is None or len(series) < 13:
        return None
    latest, year_ago = series.iloc[-1], series.iloc[-13]
    if year_ago == 0 or pd.isna(latest) or pd.isna(year_ago):
        return None
    return round((latest / year_ago - 1) * 100, 2)


def fetch_us_orders_inventory_spread():
    """
    미국 내구재 신규수주 YoY - 제조업 재고 YoY 스프레드(%p). 양수면 주문이 재고보다
    빠르게 늘어나는 확장 국면, 음수면 재고가 주문보다 빨리 쌓이는 경고 신호로 해석.
    """
    try:
        start = pd.Timestamp.today() - pd.DateOffset(years=3)
        orders = fdr.DataReader("FRED:DGORDER", start)["DGORDER"]
        inv = fdr.DataReader("FRED:MNFCTRIMSA", start)["MNFCTRIMSA"]
        orders_yoy, inv_yoy = _yoy(orders), _yoy(inv)
        if orders_yoy is None or inv_yoy is None:
            return None
        return {
            "스프레드": round(orders_yoy - inv_yoy, 2),
            "신규수주_YoY": orders_yoy, "재고_YoY": inv_yoy,
            "기준월": orders.index[-1].strftime("%Y-%m"),
        }
    except Exception:
        return None


def fetch_china_pmi():
    """
    중국 국가통계국(NBS) 공식 제조업 PMI 최신값. 50 기준 확장/위축 판단.
    chinadata.live는 비공식 제3자 집계 사이트라 서비스 중단 위험이 있음 - 실패하면 None.
    """
    try:
        r = requests.get(CHINA_PMI_URL, headers={"User-Agent": _UA}, timeout=_DEFAULT_TIMEOUT)
        r.raise_for_status()
        points = r.json()["data"]["data"]
        if not points:
            return None
        latest = points[-1]
        return {"PMI": latest["value"], "기준월": latest["date"]}
    except Exception:
        return None


def fetch_ecos_monthly_series(ecos_api_key, stat_code, item_code, months_back=30):
    """ECOS 월별 시계열(StatisticSearch)을 오래된->최신 순 pandas Series로 반환."""
    try:
        end = pd.Timestamp.today()
        start = (end - pd.DateOffset(months=months_back)).strftime("%Y%m")
        end_str = end.strftime("%Y%m")
        url = (
            f"https://ecos.bok.or.kr/api/StatisticSearch/{ecos_api_key}/json/kr/1/500/"
            f"{stat_code}/M/{start}/{end_str}/{item_code}/"
        )
        r = requests.get(url, timeout=_DEFAULT_TIMEOUT)
        r.raise_for_status()
        rows = r.json().get("StatisticSearch", {}).get("row")
        if not rows:
            return None
        df = pd.DataFrame(rows).sort_values("TIME")
        return pd.Series(
            pd.to_numeric(df["DATA_VALUE"], errors="coerce").values, index=df["TIME"].values,
        )
    except Exception:
        return None


def fetch_korea_export_yoy(ecos_api_key):
    """한국 수출금액(통관기준, ECOS 901Y118/T002) 최신월 YoY(%)."""
    series = fetch_ecos_monthly_series(ecos_api_key, "901Y118", "T002")
    yoy = _yoy(series)
    if yoy is None:
        return None
    return {"수출_YoY": yoy, "기준월": series.index[-1]}


def fetch_korea_leading_index(ecos_api_key):
    """
    선행지수 순환변동치(ECOS 901Y067/I16E) 최신값과 최근 3개월 연속 상승/하락 여부.
    통계청이 실제로 경기 전환점을 판단하는 방식(연속 반등/반락)을 그대로 따른다.
    """
    series = fetch_ecos_monthly_series(ecos_api_key, "901Y067", "I16E")
    if series is None or len(series) < 4:
        return None
    last4 = series.iloc[-4:]
    rising = bool((last4.diff().dropna() > 0).all())
    falling = bool((last4.diff().dropna() < 0).all())
    trend = "상승반전" if rising else ("하락반전" if falling else "혼조")
    return {"값": round(series.iloc[-1], 2), "추세": trend, "기준월": series.index[-1]}


def fetch_korea_inventory_cycle(ecos_api_key):
    """
    재고순환지표(생산자제품 출하 YoY% - 생산자제품 재고 YoY%, 통계청이 실제로 쓰는 정의와 동일).
    ECOS 901Y032(산업별 생산/출하/재고 지수)의 총지수(I11A) 항목 중 출하지수 원지수(구분코드 3)와
    재고지수 원지수(구분코드 5)로 계산한다 - 통계청 신규 API 키 없이 기존 ECOS 키로 바로 커버된다.
    양수면 출하가 재고보다 빨리 늘어 재고가 소진되는 확장 신호, 음수면 재고가 쌓이는 둔화 신호.
    """
    shipment = fetch_ecos_monthly_series(ecos_api_key, "901Y032", "I11A/3")
    inventory = fetch_ecos_monthly_series(ecos_api_key, "901Y032", "I11A/5")
    ship_yoy, inv_yoy = _yoy(shipment), _yoy(inventory)
    if ship_yoy is None or inv_yoy is None:
        return None
    return {
        "재고순환지표": round(ship_yoy - inv_yoy, 2), "출하_YoY": ship_yoy, "재고_YoY": inv_yoy,
        "기준월": shipment.index[-1],
    }


def fetch_korea_bond_spread(ecos_api_key):
    """국고채 10년(ECOS 817Y002/010210000) - 3년(010200000) 스프레드(%p, 일별 최신값)."""
    try:
        end = pd.Timestamp.today()
        start = (end - pd.Timedelta(days=20)).strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        def _latest(item_code):
            url = (
                f"https://ecos.bok.or.kr/api/StatisticSearch/{ecos_api_key}/json/kr/1/50/"
                f"817Y002/D/{start}/{end_str}/{item_code}/"
            )
            r = requests.get(url, timeout=_DEFAULT_TIMEOUT)
            r.raise_for_status()
            rows = r.json().get("StatisticSearch", {}).get("row")
            if not rows:
                return None, None
            rows.sort(key=lambda x: x["TIME"])
            return float(rows[-1]["DATA_VALUE"]), rows[-1]["TIME"]

        y10, date10 = _latest("010210000")
        y3, date3 = _latest("010200000")
        if y10 is None or y3 is None:
            return None
        return {"스프레드": round(y10 - y3, 3), "10년": y10, "3년": y3, "기준일": date10}
    except Exception:
        return None


def compute_quarterly_cycle_phase(ecos_api_key, n_quarters=4, months_back=30):
    """
    선행지수 순환변동치(ECOS 901Y067/I16E, 기준값 100 = 추세, 이미 추세제거된 값)를
    분기별로 리샘플링해 OECD 방식(수준 vs 모멘텀)으로 최근 n_quarters개 분기의 경기
    국면을 4분류한다(Fidelity 비즈니스 사이클 차트의 Early/Mid/Late/Recession과 대응):
      - 수준>100 & 모멘텀>0  -> Mid  (확장 전반, 가속)
      - 수준>100 & 모멘텀<=0 -> Late (확장 후반, 둔화)
      - 수준<=100 & 모멘텀<=0 -> Recession (수축)
      - 수준<=100 & 모멘텀>0  -> Early (회복)
    """
    series = fetch_ecos_monthly_series(ecos_api_key, "901Y067", "I16E", months_back=months_back)
    if series is None or len(series) < 15:
        return None
    ts = pd.Series(series.values, index=pd.to_datetime(series.index, format="%Y%m"))
    quarterly = ts.resample("QE").last().dropna()
    if len(quarterly) < n_quarters + 1:
        return None
    quarterly = quarterly.tail(n_quarters + 1)
    momentum = quarterly.diff()
    result = []
    for q in quarterly.index[1:]:
        level = quarterly.loc[q] - 100
        mom = momentum.loc[q]
        if level > 0 and mom > 0:
            phase = "Mid"
        elif level > 0 and mom <= 0:
            phase = "Late"
        elif level <= 0 and mom <= 0:
            phase = "Recession"
        else:
            phase = "Early"
        result.append({
            "분기": f"Q{q.quarter} {q.year}", "값": round(float(quarterly.loc[q]), 2),
            "모멘텀": round(float(mom), 2), "국면": phase,
        })
    return result[-n_quarters:]


def build_korea_cycle(ecos_api_key, fx_df=None):
    """
    4개 레이어를 계산해 각 레이어의 지표값·강세 여부(True/False/None)·설명을 반환한다.
    fx_df를 넘기면(원/달러 일별 OHLC) Layer 4에서 재조회하지 않고 재사용한다
    (market_dashboard.py가 이미 조회해둔 값을 그대로 넘겨 중복 호출을 피하는 용도).
    """
    layers = []

    us = fetch_us_orders_inventory_spread()
    cn = fetch_china_pmi()
    us_ok = None if us is None else us["스프레드"] > 0
    cn_ok = None if cn is None else cn["PMI"] > 50
    if us_ok is None or cn_ok is None:
        l1_verdict = None
    elif us_ok and cn_ok:
        l1_verdict = True
    elif us_ok or cn_ok:
        l1_verdict = "partial"
    else:
        l1_verdict = False
    layers.append({
        "레이어": "1. 글로벌 양대 수요", "판정": l1_verdict,
        "지표": [
            {"이름": "美 신규수주-재고 스프레드", "값": f"{us['스프레드']}%p" if us else None,
             "충족": us_ok, "기준": "0%p 초과"},
            {"이름": "中 국가통계국 제조업 PMI", "값": f"{cn['PMI']}" if cn else None,
             "충족": cn_ok, "기준": "50 초과"},
        ],
        "강세신호": "미국 스프레드>0 및 중국 PMI>50 (G2 동반 확장)",
        "약세신호": "둘 다 하회: 글로벌 총수요 둔화 / 한쪽만 충족: 지역별 온도차(부분 확장)",
    })

    kr_exp = fetch_korea_export_yoy(ecos_api_key)
    exp_ok = None if kr_exp is None else kr_exp["수출_YoY"] > 0
    layers.append({
        "레이어": "2. 실물 통관", "판정": exp_ok,
        "지표": [
            {"이름": "한국 수출금액 YoY", "값": f"{kr_exp['수출_YoY']}%" if kr_exp else None,
             "충족": exp_ok, "기준": "0% 초과"},
        ],
        "강세신호": "수출 YoY 플러스 전환/유지",
        "약세신호": "수출 YoY 마이너스: 대외 수요 둔화가 실물로 확인됨",
    })

    kr_lead = fetch_korea_leading_index(ecos_api_key)
    lead_ok = None if kr_lead is None else (
        True if kr_lead["추세"] == "상승반전" else (False if kr_lead["추세"] == "하락반전" else None)
    )
    kr_inv = fetch_korea_inventory_cycle(ecos_api_key)
    inv_ok = None if kr_inv is None else kr_inv["재고순환지표"] > 0
    if lead_ok is None or inv_ok is None:
        l3_verdict = lead_ok if inv_ok is None else inv_ok
    elif lead_ok and inv_ok:
        l3_verdict = True
    elif lead_ok or inv_ok:
        l3_verdict = "partial"
    else:
        l3_verdict = False
    layers.append({
        "레이어": "3. 공장 체력", "판정": l3_verdict,
        "지표": [
            {"이름": "선행지수 순환변동치", "값": f"{kr_lead['값']} ({kr_lead['추세']})" if kr_lead else None,
             "충족": lead_ok, "기준": "최근 3개월 연속 상승"},
            {"이름": "재고순환지표(출하YoY-재고YoY)", "값": f"{kr_inv['재고순환지표']}%p" if kr_inv else None,
             "충족": inv_ok, "기준": "0%p 초과(재고 소진 국면)"},
        ],
        "강세신호": "선행지수 순환변동치 3개월 연속 반등 및 재고순환지표 플러스(재고 소진)",
        "약세신호": "둘 다 반락/마이너스: 공장 가동 둔화 확인 / 한쪽만: 신호 엇갈림(부분 확장)",
    })

    kr_bond = fetch_korea_bond_spread(ecos_api_key)
    if fx_df is not None and len(fx_df) >= 60:
        close = fx_df["Close"]
        fx_last, fx_ma60 = close.iloc[-1], close.rolling(60).mean().iloc[-1]
        fx_ok = bool(fx_last <= fx_ma60) if pd.notna(fx_ma60) else None
    else:
        fx_last, fx_ma60, fx_ok = None, None, None
    bond_ok = None if kr_bond is None else kr_bond["스프레드"] > 0
    if fx_ok is None or bond_ok is None:
        l4_verdict = None
    else:
        l4_verdict = bool(fx_ok and bond_ok)
    layers.append({
        "레이어": "4. 유동성 밸브", "판정": l4_verdict,
        "지표": [
            {"이름": "원/달러 vs 60일 이평선",
             "값": f"{round(fx_last)}원 (이평 {round(fx_ma60)}원)" if fx_last is not None else None,
             "충족": fx_ok, "기준": "이평선 이하(하향 안정)"},
            {"이름": "국고채 10Y-3Y 스프레드", "값": f"{kr_bond['스프레드']}%p" if kr_bond else None,
             "충족": bond_ok, "기준": "0%p 초과(정상 우상향)"},
        ],
        "강세신호": "환율 하향 안정화(원화 강세) 및 장단기 스프레드 정상화",
        "약세신호": "실물 지표 호조에도 환율 급등(외국인 매도) 또는 장단기 역전: 디커플링 경고",
    })

    return layers

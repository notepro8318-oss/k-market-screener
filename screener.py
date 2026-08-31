import json
import re
import time
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import FinanceDataReader as fdr
from opendartreader import OpenDartReader
from tqdm import tqdm

DATA_DIR = Path(__file__).parent / "data"
CACHE_CSV = DATA_DIR / "screening_cache.csv"
CACHE_META = DATA_DIR / "screening_cache_meta.json"

# batch.py가 로컬(한국 IP)에서 캐시를 만들 때 쓰는 1차 필터.
# 코스피·코스닥 전 종목을 대상으로 하기 위해 시가총액/거래대금 하한을 두지 않는다.
# PER > 0 조건은 run_first_stage_screening의 Naver 데이터 자체의 구조상 항상 적용되며,
# 적자 기업(PER 계산 불가)은 이 스크리너의 성격상 애초에 대상이 아니다.
# Streamlit Cloud UI에서는 이 floor보다 느슨한 조건을 걸어도 캐시에 없는 종목은
# 결과에 나타나지 않는다 (batch.py 실행 시점에 이미 걸러졌으므로).
BROAD_CACHE_CRITERIA = {
    "MIN_MARCAP": 0,
    "MAX_PER": 9999.0,
    "MIN_TRADING_VALUE_20D": 0,
}

# opendartreader(및 그 하위 모듈)는 모든 HTTP 호출에 timeout을 전혀 지정하지 않는다.
# 서버가 응답을 늦게 주거나 아예 응답이 없는 경우(연결 자체가 거부되는 것과 달리) 예외가
# 발생하지 않아 재시도 로직조차 발동하지 못한 채 무한정 멈춰버린다. requests.Session.request를
# 패치해서 호출자가 timeout을 지정하지 않은 모든 요청에 기본 타임아웃을 강제 적용한다
# (직접 timeout=...을 넘기는 우리 코드의 호출들은 그대로 유지되므로 영향 없음).
_DEFAULT_HTTP_TIMEOUT = (10, 25)  # (연결, 응답) 초
_orig_session_request = requests.Session.request


def _session_request_with_default_timeout(self, method, url, **kwargs):
    kwargs.setdefault("timeout", _DEFAULT_HTTP_TIMEOUT)
    return _orig_session_request(self, method, url, **kwargs)


requests.Session.request = _session_request_with_default_timeout

# ==========================================
# 기본 필터링 조건 (UI/CLI 모두 이 값을 기본값으로 사용)
# ==========================================
DEFAULT_FILTER_CRITERIA = {
    "MARKET": "전체",                    # "전체" | "KOSPI" | "KOSDAQ"
    "MIN_MARCAP": 80_000_000_000,        # 시총 800억 이상
    "MAX_PER": 12.0,                     # PER 12배 이하 (0 초과)
    "MAX_PBR": 1.3,                      # PBR 1.3배 이하 (0 초과)
    "MIN_OPM": 10.0,                     # 최근 결산 영업이익률 10% 이상
    "MIN_ROA": 7.0,                      # ROA 7% 이상
    "MIN_ROE": 10.0,                     # ROE 10% 이상
    "MIN_TRADING_VALUE_20D": 500_000_000,  # 20일 평균 거래대금 5억 이상
    "MIN_FSCORE": 7,                     # 최종 Piotroski F-Score 컷오프
}


def create_dart_client(dart_api_key, retries=4, delay=5, log=print):
    """
    OpenDartReader()는 최초 생성 시 DART 서버에서 전체 기업코드 목록을 내려받는데,
    이 호출에 재시도 로직이 없어 일시적인 네트워크 지연/타임아웃에도 바로 실패한다.
    (성공하면 그날 하루치를 docs_cache/에 캐싱해두므로 이후 호출은 즉시 재사용된다.)
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return OpenDartReader(dart_api_key)
        except requests.exceptions.RequestException as e:
            last_err = e
            log(f"⚠ DART 서버 연결 실패 ({attempt}/{retries}차 시도): {e.__class__.__name__} - {delay}초 후 재시도...")
            time.sleep(delay)
    raise last_err


def _fetch_naver_per_table(sosok):
    """Naver 시가총액 페이지에서 종목코드별 PER을 스크래핑 (로그인 불필요)."""
    headers = {"User-Agent": "Mozilla/5.0"}
    base_url = "https://finance.naver.com/sise/sise_market_sum.naver"

    resp = requests.get(base_url, params={"sosok": sosok, "page": 1}, headers=headers, timeout=10)
    resp.encoding = "euc-kr"
    m = re.search(r'pgRR">\s*<a href="[^"]*page=(\d+)"', resp.text)
    last_page = int(m.group(1)) if m else 1

    per_rows = []
    for page in range(1, last_page + 1):
        resp = requests.get(base_url, params={"sosok": sosok, "page": page}, headers=headers, timeout=10)
        resp.encoding = "euc-kr"
        codes = list(dict.fromkeys(re.findall(r"code=(\d{6})", resp.text)))

        tables = pd.read_html(StringIO(resp.text))
        table = tables[1].dropna(how="all").reset_index(drop=True)
        if len(codes) != len(table):
            continue

        table["종목코드"] = codes
        per_rows.append(table[["종목코드", "PER"]])

    df_per = pd.concat(per_rows, ignore_index=True)
    df_per["PER"] = pd.to_numeric(df_per["PER"], errors="coerce")
    df_per = df_per.dropna(subset=["PER"]).set_index("종목코드")
    return df_per


def run_first_stage_screening(criteria, log=print, progress_cb=None):
    """
    KRX data.krx.co.kr API가 로그인을 요구하게 되어, 로그인 불필요한
    FinanceDataReader + Naver 시가총액 페이지 스크래핑으로 대체함.
    PBR은 KRX에서 더 이상 무료로 제공되지 않아 2단계 DART 재무데이터로 계산.
    """
    import datetime

    today = datetime.datetime.today()
    month_ago = today - datetime.timedelta(days=35)

    log("▶ [1단계] 전종목 시장 데이터 수집 및 1차 필터링 시작...")

    # 1. 전종목 시가총액 수집 (FinanceDataReader, 로그인 불필요)
    # KOSDAQ GLOBAL은 코스닥의 하위 세그먼트이므로 KOSDAQ으로 합치고, KONEX는 이 스크리너의
    # 대상(코스피·코스닥)이 아니므로 제외한다.
    df_listing = fdr.StockListing("KRX")
    df_listing = df_listing.set_index("Code")[["Name", "Market", "Marcap"]]
    df_listing.columns = ["종목명", "시장구분", "시가총액"]
    df_listing["시장구분"] = df_listing["시장구분"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    df_listing = df_listing[df_listing["시장구분"].isin(["KOSPI", "KOSDAQ"])]

    # 2. 전종목 PER 수집 (Naver 시가총액 페이지 스크래핑, 로그인 불필요)
    log("▷ PER 데이터 수집 중...")
    df_per = pd.concat([_fetch_naver_per_table(0), _fetch_naver_per_table(1)])
    df_per = df_per[~df_per.index.duplicated(keep="first")]

    df_market = df_listing.join(df_per, how="inner")

    # 3. 시가총액 + PER 필터로 후보군을 먼저 축소 (거래대금 조회 비용 절감)
    cond_cap = df_market["시가총액"] >= criteria["MIN_MARCAP"]
    cond_per = (df_market["PER"] > 0) & (df_market["PER"] <= criteria["MAX_PER"])
    df_narrowed = df_market[cond_cap & cond_per].copy()
    log(f"✔ 시가총액/PER 필터 통과: {len(df_narrowed)}개 종목")

    # 4. 후보군에 한해 20영업일 평균 거래대금 계산 (종가 x 거래량으로 근사)
    log("▷ 20영업일 평균 거래대금 집계 중...")
    trading_val_list = []
    total = len(df_narrowed)
    for i, ticker in enumerate(tqdm(df_narrowed.index)):
        try:
            df_ohlcv = fdr.DataReader(ticker, month_ago, today)
            avg_val_20d = (df_ohlcv["Close"] * df_ohlcv["Volume"]).tail(20).mean() if not df_ohlcv.empty else 0
        except Exception:
            avg_val_20d = 0
        trading_val_list.append(avg_val_20d)
        if progress_cb:
            progress_cb("trading_value", i + 1, total)

    df_narrowed["20D_Avg_Trading_Val"] = trading_val_list

    cond_trd = df_narrowed["20D_Avg_Trading_Val"] >= criteria["MIN_TRADING_VALUE_20D"]
    df_stage1 = df_narrowed[cond_trd].copy()
    log(f"✔ 거래대금 필터 통과: {len(df_stage1)}개 종목")

    return df_stage1


# ==========================================
# 2단계: 재무제표 파싱 (TTM 기준), 수익성(OPM/ROA/ROE) 및 F-Score 연산
# ==========================================
NET_INCOME_NAMES = [
    "당기순이익", "당기순이익(손실)", "연결당기순이익",
    "분기순이익", "분기순이익(손실)",
    "반기순이익", "반기순이익(손실)",
]
REVENUE_NAMES = ["매출액", "매출", "수익(매출액)", "수익", "영업수익"]
OP_INCOME_NAMES = ["영업이익", "영업이익(손실)"]
GROSS_PROFIT_NAMES = ["매출총이익", "매출총이익(손실)"]
CFO_NAMES = [
    "영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동 현금흐름", "영업활동으로인한순현금흐름",
]
# 현행 K-IFRS 재무제표에는 "경상이익" 계정이 없어 세전이익(법인세비용차감전순이익)으로 대체한다.
PRETAX_INCOME_NAMES = [
    "법인세비용차감전순이익(손실)", "법인세비용차감전순이익", "법인세비용차감전순손실", "법인세비용차감전순손익",
    "법인세차감전순이익(손실)", "법인세차감전순이익", "법인세차감전순손실", "법인세차감전순손익",
    "법인세비용차감전계속영업이익(손실)", "법인세비용차감전계속영업이익", "법인세비용차감전계속영업손실", "법인세비용차감전계속영업손익",
]
CFI_NAMES = [
    "투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동 현금흐름", "투자활동으로인한순현금흐름",
]
CFF_NAMES = [
    "재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동 현금흐름", "재무활동으로인한순현금흐름",
]
# 기업분석 체크리스트(건전성/성장성 지표)용 재무상태표·손익계산서 계정
TOTAL_LIABILITIES_NAMES = ["부채총계"]
TOTAL_EQUITY_NAMES = ["자본총계"]
CURRENT_ASSETS_NAMES = ["유동자산"]
CURRENT_LIABILITIES_NAMES = ["유동부채"]
INVENTORY_NAMES = ["재고자산", "유동재고자산"]
RETAINED_EARNINGS_NAMES = ["이익잉여금(결손금)", "이익잉여금"]
CAPITAL_SURPLUS_NAMES = ["자본잉여금", "주식발행초과금"]
CAPITAL_STOCK_NAMES = ["자본금", "보통주자본금"]
RECEIVABLES_NAMES = ["매출채권", "매출채권및기타유동채권", "매출채권및기타채권"]
COGS_NAMES = ["매출원가"]
# "이자비용"이 본문 계정으로 안 잡히면 현금흐름표의 이자 지급액(현금기준)으로 근사한다.
INTEREST_EXPENSE_NAMES = ["이자비용"]
INTEREST_PAID_CF_NAMES = ["이자의지급", "이자지급(영업)", "이자지급"]


_ACCOUNT_PREFIX_RE = re.compile(r"^(?:[Ⅰ-Ⅻ]+[.\s]*|[IVX]+\.\s*)")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_account_name(name):
    """DART 계정명의 로마숫자 항목번호(Ⅰ./Ⅱ./...) 접두사를 제거하고 공백을 없애 비교용으로 정규화."""
    return _WHITESPACE_RE.sub("", _ACCOUNT_PREFIX_RE.sub("", str(name)))


def clean_num(val):
    if pd.isna(val) or val == "" or val == "-":
        return 0.0
    return float(str(val).replace(",", "").strip())


def _amount(row, candidates):
    """row에서 candidates 순서대로 값이 있는(NaN이 아닌) 첫 컬럼값을 반환."""
    for col in candidates:
        if col in row.index:
            val = row[col]
            if pd.notna(val) and str(val).strip() not in ("", "-"):
                return clean_num(val)
    return None


def get_amount(df, account_names, current=True):
    """
    계정값을 시점/누적 컬럼 우선순위에 따라 추출한다.
    - current=True : 당기 값. 분기/반기 보고서는 당기누적(thstrm_add_amount, YTD)을 우선 사용하고
      없으면(BS 항목이거나 사업보고서) thstrm_amount(시점값/연간값)로 대체한다.
    - current=False: 비교 시점 값. 전년동기누적(frmtrm_add_amount)을 우선 사용하고, 없으면
      frmtrm_q_amount(전년동기 단일분기, CF 등에 쓰임), 그마저 없으면 frmtrm_amount
      (사업보고서의 전기 전체값 또는 BS의 전기말 잔액)로 대체한다.
    이 우선순위 덕분에 사업보고서(연간)·반기·분기 보고서, BS·IS·CF 항목 모두 동일한 함수로
    올바르게 처리된다.

    계정명 비교는 앞의 로마숫자 항목번호(Ⅰ./Ⅱ./...)를 떼어내고 공백을 모두 제거한 뒤
    매칭한다 - 같은 계정이라도 회사/연도(심지어 같은 회사의 다른 사업연도)별로
    "영업활동현금흐름" / "영업활동 현금흐름" / "Ⅰ.영업활동으로인한현금흐름"처럼 항목번호
    유무와 띄어쓰기가 제각각이라, 정확히 일치해야 하는 방식으로는 계속 놓치는 사례가 나온다.
    """
    account_nm_norm = df["account_nm"].astype(str).apply(_normalize_account_name)
    for name in account_names:
        target = _normalize_account_name(name)
        matched = df[account_nm_norm == target]
        if matched.empty:
            continue
        row = matched.iloc[0]
        if current:
            val = _amount(row, ["thstrm_add_amount", "thstrm_amount"])
        else:
            val = _amount(row, ["frmtrm_add_amount", "frmtrm_q_amount", "frmtrm_amount"])
        if val is not None:
            return val
    return 0.0


def _report_candidates(as_of):
    """
    as_of 시점까지 공시되었을 것으로 기대되는 (사업연도, reprt_code)를 최신순으로 반환.
    DART 공시 마감일: 사업보고서 3/31, 1분기 5/15, 반기 8/14, 3분기 11/14 (달력 연도 기준).
    """
    as_of = pd.Timestamp(as_of)
    y = as_of.year
    deadlines = [
        (pd.Timestamp(y, 3, 31), y - 1, "11011"),
        (pd.Timestamp(y, 5, 15), y, "11013"),
        (pd.Timestamp(y, 8, 14), y, "11012"),
        (pd.Timestamp(y, 11, 14), y, "11014"),
    ]
    passed = [(yr, code) for dl, yr, code in deadlines if as_of >= dl]
    candidates = list(reversed(passed))
    # 예상보다 공시가 늦었을 경우를 대비한 안전망
    candidates += [
        (y - 1, "11014"), (y - 1, "11012"), (y - 1, "11013"), (y - 1, "11011"),
        (y - 2, "11011"),
    ]
    seen = set()
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _fetch_report(dart, ticker, bsns_year, reprt_code):
    try:
        df_fs = dart.finstate_all(ticker, bsns_year, reprt_code=reprt_code, fs_div="CFS")
        if df_fs is None or df_fs.empty:
            df_fs = dart.finstate_all(ticker, bsns_year, reprt_code=reprt_code, fs_div="OFS")
        if df_fs is None or df_fs.empty:
            return None
        return df_fs
    except Exception:
        return None


_PERIOD_END = {"11011": (12, 31), "11012": (6, 30), "11013": (3, 31), "11014": (9, 30)}


def _report_reference_date(df, year, code):
    """공시 접수일(rcept_no 앞 8자리, YYYYMMDD)을 YYYY-MM-DD로 반환. 없으면 결산기준일로 대체."""
    try:
        rcept = str(df.iloc[0]["rcept_no"])
        if len(rcept) >= 8 and rcept[:8].isdigit():
            d = rcept[:8]
            return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    except Exception:
        pass
    m, d = _PERIOD_END.get(code, (12, 31))
    return f"{year}-{m:02d}-{d:02d}"


def _annual_year_metrics(df):
    """사업보고서 하나에서 당기(thstrm)·전기(frmtrm) 두 사업연도의 원본 재무값을 추출."""
    bs = df[df["sj_div"] == "BS"]
    is_df = df[df["sj_div"].isin(["IS", "CIS"])]

    def pair(names, section):
        return get_amount(section, names, current=True), get_amount(section, names, current=False)

    revenue_t, revenue_p = pair(REVENUE_NAMES, is_df)
    op_income_t, op_income_p = pair(OP_INCOME_NAMES, is_df)
    net_income_t, net_income_p = pair(NET_INCOME_NAMES, is_df)
    total_assets_t, total_assets_p = pair(["자산총계"], bs)
    total_equity_t, total_equity_p = pair(["자본총계"], bs)

    cur = {"revenue": revenue_t, "op_income": op_income_t, "net_income": net_income_t,
           "total_assets": total_assets_t, "total_equity": total_equity_t}
    prev = {"revenue": revenue_p, "op_income": op_income_p, "net_income": net_income_p,
            "total_assets": total_assets_p, "total_equity": total_equity_p}
    return cur, prev


def _ratios_from(vals, marcap):
    """_annual_year_metrics()가 반환한 원본값 딕셔너리 + 그 시점 시가총액으로 비율 계산. 계산 불가하면 None."""
    revenue, op_income, net_income = vals["revenue"], vals["op_income"], vals["net_income"]
    total_assets, total_equity = vals["total_assets"], vals["total_equity"]
    opm = (op_income / revenue * 100) if revenue > 0 else None
    roa = (net_income / total_assets * 100) if total_assets > 0 else None
    roe = (net_income / total_equity * 100) if total_equity > 0 else None
    pbr = (marcap / total_equity) if (marcap and total_equity > 0) else None
    per = (marcap / net_income) if (marcap and net_income and net_income > 0) else None
    return {"OPM": opm, "ROA": roa, "ROE": roe, "PBR": pbr, "PER": per}


def _annual_year_financials(df):
    """
    사업보고서 하나에서 당기(thstrm)·전기(frmtrm) 두 사업연도의 매출/세전이익/CFO/CFI/CFF와
    기업분석 체크리스트(건전성/성장성)에 필요한 재무상태표·손익계산서 원본값을 함께 추출.
    """
    is_df = df[df["sj_div"].isin(["IS", "CIS"])]
    cf = df[df["sj_div"] == "CF"]
    bs = df[df["sj_div"] == "BS"]

    def pair(names, section):
        return get_amount(section, names, current=True), get_amount(section, names, current=False)

    revenue_t, revenue_p = pair(REVENUE_NAMES, is_df)
    pretax_t, pretax_p = pair(PRETAX_INCOME_NAMES, is_df)
    op_income_t, op_income_p = pair(OP_INCOME_NAMES, is_df)
    cogs_t, cogs_p = pair(COGS_NAMES, is_df)
    cfo_t, cfo_p = pair(CFO_NAMES, cf)
    cfi_t, cfi_p = pair(CFI_NAMES, cf)
    cff_t, cff_p = pair(CFF_NAMES, cf)

    interest_t, interest_p = pair(INTEREST_EXPENSE_NAMES, is_df)
    if interest_t == 0.0 and interest_p == 0.0:
        interest_t, interest_p = pair(INTEREST_PAID_CF_NAMES, cf)  # 이자비용 미기재 시 현금 이자지급액으로 근사

    total_liab_t, total_liab_p = pair(TOTAL_LIABILITIES_NAMES, bs)
    total_equity_t, total_equity_p = pair(TOTAL_EQUITY_NAMES, bs)
    current_assets_t, current_assets_p = pair(CURRENT_ASSETS_NAMES, bs)
    current_liab_t, current_liab_p = pair(CURRENT_LIABILITIES_NAMES, bs)
    inventory_t, inventory_p = pair(INVENTORY_NAMES, bs)
    retained_t, retained_p = pair(RETAINED_EARNINGS_NAMES, bs)
    cap_surplus_t, cap_surplus_p = pair(CAPITAL_SURPLUS_NAMES, bs)
    cap_stock_t, cap_stock_p = pair(CAPITAL_STOCK_NAMES, bs)
    receivables_t, receivables_p = pair(RECEIVABLES_NAMES, bs)

    cur = {
        "revenue": revenue_t, "pretax_income": pretax_t, "op_income": op_income_t, "cogs": cogs_t,
        "cfo": cfo_t, "cfi": cfi_t, "cff": cff_t, "interest_expense": interest_t,
        "total_liabilities": total_liab_t, "total_equity": total_equity_t,
        "current_assets": current_assets_t, "current_liabilities": current_liab_t,
        "inventory": inventory_t, "retained_earnings": retained_t,
        "capital_surplus": cap_surplus_t, "capital_stock": cap_stock_t, "receivables": receivables_t,
    }
    prev = {
        "revenue": revenue_p, "pretax_income": pretax_p, "op_income": op_income_p, "cogs": cogs_p,
        "cfo": cfo_p, "cfi": cfi_p, "cff": cff_p, "interest_expense": interest_p,
        "total_liabilities": total_liab_p, "total_equity": total_equity_p,
        "current_assets": current_assets_p, "current_liabilities": current_liab_p,
        "inventory": inventory_p, "retained_earnings": retained_p,
        "capital_surplus": cap_surplus_p, "capital_stock": cap_stock_p, "receivables": receivables_p,
    }
    return cur, prev


def compute_5y_financials(dart, ticker, as_of, years=5):
    """
    최근 {years}개 사업연도(사업보고서 기준)의 매출액·경상이익(세전이익 근사)·경상이익률·
    영업활동현금흐름·투자활동현금흐름·재무활동현금흐름·잉여현금흐름(FCF ≈ CFO + CFI)과,
    기업분석 체크리스트의 건전성(부채비율/당좌비율/이자보상배율/유보율)·성장성(매출성장률/
    매출채권회전율/재고자산회전율) 지표를 함께 계산한다. 이미 조회한 사업보고서에서 더 많은
    계정을 뽑아내는 것뿐이라 DART 호출이 추가로 들지는 않는다.

    사업보고서 1건이 당기·전기 두 사업연도 값을 함께 내려주므로, 격년으로 조회하면
    {years}개 연도를 확보하는 데 사업보고서 3회 조회(5개년 기준)면 충분하다.
    특정 연도를 못 구하면 그 해는 건너뛴다 (리스트 길이가 {years}보다 짧아질 수 있음).
    """
    as_of = pd.Timestamp(as_of)
    base_year = as_of.year - 1 if as_of >= pd.Timestamp(as_of.year, 3, 31) else as_of.year - 2

    year_data = {}
    probe_year = base_year
    attempts = 0
    max_attempts = years + 2  # 격년 조회 실패 시 재시도 여유
    while len(year_data) < years and attempts < max_attempts:
        df_annual = _fetch_report(dart, ticker, probe_year, "11011")
        if df_annual is not None:
            cur, prev = _annual_year_financials(df_annual)
            year_data.setdefault(probe_year, cur)
            year_data.setdefault(probe_year - 1, prev)
        probe_year -= 2
        attempts += 1

    if not year_data:
        return None

    years_sorted = sorted(year_data.keys())[-years:]
    out = {
        "연도": years_sorted,
        "매출액": [], "경상이익": [], "경상이익률(%)": [],
        "영업활동현금흐름": [], "투자활동현금흐름": [], "재무활동현금흐름": [], "잉여현금흐름": [],
        "부채비율(%)": [], "당좌비율(%)": [], "이자보상배율": [], "유보율(%)": [],
        "매출성장률(%)": [], "매출채권회전율": [], "재고자산회전율": [],
    }
    for yr in years_sorted:
        v = year_data[yr]
        revenue, pretax = v["revenue"], v["pretax_income"]
        cfo, cfi, cff = v["cfo"], v["cfi"], v["cff"]
        out["매출액"].append(round(revenue))
        out["경상이익"].append(round(pretax))
        out["경상이익률(%)"].append(round(pretax / revenue * 100, 2) if revenue else 0.0)
        out["영업활동현금흐름"].append(round(cfo))
        out["투자활동현금흐름"].append(round(cfi))
        out["재무활동현금흐름"].append(round(cff))
        out["잉여현금흐름"].append(round(cfo + cfi))

        total_liab, total_equity = v["total_liabilities"], v["total_equity"]
        current_assets, current_liab = v["current_assets"], v["current_liabilities"]
        inventory = v["inventory"]
        retained, cap_surplus, cap_stock = v["retained_earnings"], v["capital_surplus"], v["capital_stock"]
        op_income, interest_expense = v["op_income"], v["interest_expense"]
        cogs, receivables = v["cogs"], v["receivables"]

        out["부채비율(%)"].append(round(total_liab / total_equity * 100, 2) if total_equity else 0.0)
        quick_assets = current_assets - inventory
        out["당좌비율(%)"].append(round(quick_assets / current_liab * 100, 2) if current_liab else 0.0)
        out["이자보상배율"].append(round(op_income / interest_expense, 2) if interest_expense else 0.0)
        out["유보율(%)"].append(round((retained + cap_surplus) / cap_stock * 100, 2) if cap_stock else 0.0)

        prev_v = year_data.get(yr - 1)
        prev_revenue = prev_v["revenue"] if prev_v else 0
        out["매출성장률(%)"].append(
            round((revenue - prev_revenue) / prev_revenue * 100, 2) if prev_revenue else 0.0
        )

        avg_receivables = (receivables + prev_v["receivables"]) / 2 if prev_v else receivables
        out["매출채권회전율"].append(round(revenue / avg_receivables, 2) if avg_receivables else 0.0)

        avg_inventory = (inventory + prev_v["inventory"]) / 2 if prev_v else inventory
        out["재고자산회전율"].append(round(cogs / avg_inventory, 2) if avg_inventory else 0.0)
    return out


def compute_raw_metrics(dart, ticker, as_of, marcap, max_report_attempts=4, include_trend=False):
    """
    as_of 시점까지 공시된 가장 최근 보고서(사업/반기/분기)를 찾아 TTM(최근 12개월 합산)
    기준으로 매출·영업이익·순이익·매출총이익·영업활동현금흐름을 계산하고, OPM/ROA/ROE/PBR/PER/
    Piotroski F-Score를 산출한다. 조건(criteria) 필터링은 하지 않고 계산된 값을 그대로 반환한다
    (배치 캐시처럼 나중에 임의 조건으로 다시 걸러낼 원본 지표가 필요한 경우에 사용).

    include_trend=True이면 과거 최대 3개 사업연도 + 현재 TTM의 OPM/ROA/ROE/PBR/PER을
    *_trend 리스트로 함께 반환한다 (스파크라인용). DART 조회 1회, FinanceDataReader 조회 1회가
    추가로 필요해 호출 비용이 늘어나므로, 이 값이 실제로 쓰이는 배치 캐시 생성(batch.py)에서만
    켠다 - 백테스트/CLI 등 다른 호출자는 기본값(False)을 그대로 쓴다.

    TTM = 직전 사업연도 전체 - 전년동기누적 + 당기누적
    (분기/반기 보고서는 DART가 전년동기누적(frmtrm_add_amount)을 함께 내려주므로 추가 조회 없이
    계산 가능하고, 직전 사업연도 전체만 사업보고서를 한 번 더 조회해서 얻는다. 가장 최근 보고서가
    이미 사업보고서이면 그 값 자체가 TTM이므로 추가 조회가 필요 없다.)

    재무상태표(BS) 항목은 시점값이므로 항상 가장 최근 보고서의 당기말 값을 사용하고,
    F-Score의 전년 대비 비교는 재무상태표는 직전 사업연도말(전기), 손익 항목은 직전
    사업연도 전체(당기 대비 한 해 전 값)를 기준으로 한다.
    """
    marcap = float(marcap)  # pandas/numpy 스칼라가 넘어와도 JSON 직렬화 가능한 순수 float로 통일
    candidates = _report_candidates(as_of)[:max_report_attempts]

    df_current = None
    current_year = current_code = None
    for yr, code in candidates:
        df_current = _fetch_report(dart, ticker, yr, code)
        if df_current is not None:
            current_year, current_code = yr, code
            break
    if df_current is None:
        return None

    bs = df_current[df_current["sj_div"] == "BS"]
    is_df = df_current[df_current["sj_div"].isin(["IS", "CIS"])]
    cf = df_current[df_current["sj_div"] == "CF"]

    # 재무상태표: 항상 가장 최근 보고서의 당기말/전기말 값 사용 (시점값이라 TTM 개념 불필요)
    total_assets_t = get_amount(bs, ["자산총계"], current=True)
    total_assets_prev = get_amount(bs, ["자산총계"], current=False)
    total_equity_t = get_amount(bs, ["자본총계"], current=True)
    current_assets_t = get_amount(bs, ["유동자산"], current=True)
    current_assets_prev = get_amount(bs, ["유동자산"], current=False)
    current_liab_t = get_amount(bs, ["유동부채"], current=True)
    current_liab_prev = get_amount(bs, ["유동부채"], current=False)
    non_current_liab_t = get_amount(bs, ["비유동부채", "장기차입금"], current=True)
    non_current_liab_prev = get_amount(bs, ["비유동부채", "장기차입금"], current=False)
    cap_t = get_amount(bs, ["자본금"], current=True)
    cap_prev = get_amount(bs, ["자본금"], current=False)

    if current_code == "11011":
        # 이미 사업보고서 -> 당기 자체가 TTM, 전기는 그 직전 사업연도
        revenue_t = get_amount(is_df, REVENUE_NAMES, current=True)
        revenue_prev = get_amount(is_df, REVENUE_NAMES, current=False)
        op_income_t = get_amount(is_df, OP_INCOME_NAMES, current=True)
        net_income_t = get_amount(is_df, NET_INCOME_NAMES, current=True)
        net_income_prev = get_amount(is_df, NET_INCOME_NAMES, current=False)
        gross_profit_t = get_amount(is_df, GROSS_PROFIT_NAMES, current=True)
        gross_profit_prev = get_amount(is_df, GROSS_PROFIT_NAMES, current=False)
        cfo_t = get_amount(cf, CFO_NAMES, current=True)
    else:
        # 분기/반기 보고서 -> TTM = 직전 사업연도 전체 - 전년동기누적 + 당기누적
        df_annual = None
        for yr_try in (current_year - 1, current_year - 2):
            df_annual = _fetch_report(dart, ticker, yr_try, "11011")
            if df_annual is not None:
                break
        if df_annual is None:
            return None  # TTM 계산에 필요한 직전 사업보고서를 찾을 수 없음

        is_annual = df_annual[df_annual["sj_div"].isin(["IS", "CIS"])]
        cf_annual = df_annual[df_annual["sj_div"] == "CF"]

        def ttm_value(names, cur_section, annual_section):
            """TTM = 직전 사업연도 전체 - 전년동기누적 + 당기누적."""
            fy_prior = get_amount(annual_section, names, current=True)
            ytd_prior_same_period = get_amount(cur_section, names, current=False)
            ytd_current = get_amount(cur_section, names, current=True)
            return fy_prior - ytd_prior_same_period + ytd_current

        revenue_t = ttm_value(REVENUE_NAMES, is_df, is_annual)
        op_income_t = ttm_value(OP_INCOME_NAMES, is_df, is_annual)
        net_income_t = ttm_value(NET_INCOME_NAMES, is_df, is_annual)
        gross_profit_t = ttm_value(GROSS_PROFIT_NAMES, is_df, is_annual)
        cfo_t = ttm_value(CFO_NAMES, cf, cf_annual)

        # F-Score의 "전기" 비교 기준: 직전 사업연도 전체 (한 해 전 TTM에 대한 근사)
        revenue_prev = get_amount(is_annual, REVENUE_NAMES, current=True)
        net_income_prev = get_amount(is_annual, NET_INCOME_NAMES, current=True)
        gross_profit_prev = get_amount(is_annual, GROSS_PROFIT_NAMES, current=True)

    if gross_profit_t == 0.0:
        gross_profit_t, gross_profit_prev = op_income_t, op_income_t

    # OPM/ROA/ROE/PBR/PER - 전부 TTM/최신 시점 기준
    opm = (op_income_t / revenue_t * 100) if revenue_t > 0 else 0
    roa = (net_income_t / total_assets_t * 100) if total_assets_t > 0 else 0
    roe = (net_income_t / total_equity_t * 100) if total_equity_t > 0 else 0
    pbr = (marcap / total_equity_t) if total_equity_t > 0 else 0
    per = (marcap / net_income_t) if net_income_t > 0 else 0

    # 피오트로스키 F-Score (9개 항목) 계산
    scores = 0
    # 수익성 (4점)
    scores += 1 if roa > 0 else 0
    scores += 1 if cfo_t > 0 else 0
    roa_prev = (net_income_prev / total_assets_prev * 100) if total_assets_prev > 0 else 0
    scores += 1 if roa > roa_prev else 0
    scores += 1 if cfo_t > net_income_t else 0

    # 건전성 (3점)
    lev_t = (non_current_liab_t / total_assets_t) if total_assets_t > 0 else 0
    lev_prev = (non_current_liab_prev / total_assets_prev) if total_assets_prev > 0 else 0
    scores += 1 if lev_t <= lev_prev else 0

    cr_t = (current_assets_t / current_liab_t) if current_liab_t > 0 else 0
    cr_prev = (current_assets_prev / current_liab_prev) if current_liab_prev > 0 else 0
    scores += 1 if cr_t > cr_prev else 0

    # 자본금 변동 기준 주식수 비희석 체크
    scores += 1 if cap_t <= cap_prev else 0

    # 영업효율성 (2점)
    gpm_t = (gross_profit_t / revenue_t) if revenue_t > 0 else 0
    gpm_prev = (gross_profit_prev / revenue_prev) if revenue_prev > 0 else 0
    scores += 1 if gpm_t > gpm_prev else 0

    turn_t = (revenue_t / total_assets_t) if total_assets_t > 0 else 0
    turn_prev = (revenue_prev / total_assets_prev) if total_assets_prev > 0 else 0
    scores += 1 if turn_t > turn_prev else 0

    result = {
        "OPM(%)": round(opm, 2),
        "ROA(%)": round(roa, 2),
        "ROE(%)": round(roe, 2),
        "PBR": round(pbr, 2),
        "PER": round(per, 2),
        "F_Score": scores,
        "기준보고서": _report_reference_date(df_current, current_year, current_code),
    }

    if include_trend:
        # 과거 최대 3개 사업연도 + 현재 TTM
        try:
            price_hist = fdr.DataReader(ticker, pd.Timestamp(as_of) - pd.DateOffset(years=5), pd.Timestamp(as_of))
        except Exception:
            price_hist = None
        latest_close = price_hist.iloc[-1]["Close"] if price_hist is not None and not price_hist.empty else None

        def marcap_at_year_end(year):
            if price_hist is None or price_hist.empty or not latest_close:
                return None
            row = price_hist[price_hist.index <= pd.Timestamp(year, 12, 31)]
            if row.empty:
                return None
            return row.iloc[-1]["Close"] * (marcap / latest_close)

        year_metrics = {}
        if current_code == "11011":
            cur_y, prev_y = _annual_year_metrics(df_current)
            year_metrics[current_year - 1] = prev_y
            probe_year = current_year - 2
        else:
            annual_cur, annual_prev = _annual_year_metrics(df_annual)
            year_metrics[current_year - 2] = annual_prev
            year_metrics[current_year - 1] = annual_cur
            probe_year = current_year - 3

        df_older = _fetch_report(dart, ticker, probe_year, "11011")
        if df_older is None:
            df_older = _fetch_report(dart, ticker, probe_year - 1, "11011")
        if df_older is not None:
            older_cur, older_prev = _annual_year_metrics(df_older)
            year_metrics[probe_year] = older_cur
            year_metrics[probe_year - 1] = older_prev

        trend = {"OPM": [], "ROA": [], "ROE": [], "PBR": [], "PER": []}
        for yr in sorted(year_metrics.keys())[-3:]:
            r = _ratios_from(year_metrics[yr], marcap_at_year_end(yr))
            for key in trend:
                if r[key] is not None:
                    trend[key].append(round(float(r[key]), 2))
        trend["OPM"].append(round(opm, 2))
        trend["ROA"].append(round(roa, 2))
        trend["ROE"].append(round(roe, 2))
        trend["PBR"].append(round(pbr, 2))
        trend["PER"].append(round(per, 2))

        result["OPM_trend"] = trend["OPM"]
        result["ROA_trend"] = trend["ROA"]
        result["ROE_trend"] = trend["ROE"]
        result["PBR_trend"] = trend["PBR"]
        result["PER_trend"] = trend["PER"]

    return result


def evaluate_financials_and_fscore(dart, ticker, as_of, marcap, criteria, require_per=False, max_report_attempts=4):
    """
    compute_raw_metrics()로 계산한 원본 지표에 criteria(OPM/ROA/ROE/PBR 최소·최대 조건)를
    적용해 통과 종목만 반환한다 (조건 미달성 시 None).

    require_per=True이면 PER도 (marcap / TTM 당기순이익)으로 직접 계산한 값을 MAX_PER 조건까지
    검증하고 결과에 포함한다. 백테스트처럼 특정 과거 시점의 실시간 PER 스냅샷을 구할 수 없는
    경우에 사용한다 (실시간 스크리닝은 1단계에서 Naver PER을 이미 적용했으므로 기본값 False이고,
    이 경우 PER은 결과에 포함되지 않는다 - 호출자가 Naver PER을 별도로 붙인다).
    """
    metrics = compute_raw_metrics(dart, ticker, as_of, marcap, max_report_attempts)
    if metrics is None:
        return None

    fail = (metrics["OPM(%)"] < criteria["MIN_OPM"] or
            metrics["ROA(%)"] < criteria["MIN_ROA"] or
            metrics["ROE(%)"] < criteria["MIN_ROE"] or
            not (0 < metrics["PBR"] <= criteria["MAX_PBR"]))
    if require_per:
        fail = fail or not (0 < metrics["PER"] <= criteria["MAX_PER"])
    if fail:
        return None

    result = dict(metrics)
    if not require_per:
        del result["PER"]
    return result


# ==========================================
# 3단계: 통합 실행
# ==========================================
def run_pipeline(dart_api_key, criteria, as_of=None, log=print, progress_cb=None):
    """
    전체 스크리닝 파이프라인을 실행하고 최종 결과 DataFrame을 반환한다.
    as_of를 지정하지 않으면 오늘 시점 기준으로 가장 최근 공시된 사업/반기/분기보고서를
    찾아 TTM(최근 12개월) 재무데이터로 평가한다.
    """
    if as_of is None:
        as_of = pd.Timestamp.today()

    dart = create_dart_client(dart_api_key, log=log)
    df_candidates = run_first_stage_screening(criteria, log=log, progress_cb=progress_cb)

    results = []
    log(f"\n▶ [2단계] 1차 통과 {len(df_candidates)}개 종목 수익성 필터 및 F-Score 검증 시작... (TTM 기준일: {pd.Timestamp(as_of).date()})")

    total = len(df_candidates)
    for i, ticker in enumerate(tqdm(df_candidates.index)):
        name = df_candidates.loc[ticker, "종목명"]
        marcap = df_candidates.loc[ticker, "시가총액"]
        metrics = evaluate_financials_and_fscore(dart, ticker, as_of, marcap, criteria)

        if metrics is not None:
            marcap_eok = round(marcap / 100_000_000)
            avg_trd_eok = round(df_candidates.loc[ticker, "20D_Avg_Trading_Val"] / 100_000_000, 1)
            per = df_candidates.loc[ticker, "PER"]
            pbr = metrics["PBR"]

            results.append({
                "종목코드": ticker,
                "종목명": name,
                "시가총액(억)": marcap_eok,
                "20일거래대금(억)": avg_trd_eok,
                "PER": per,
                "PBR": pbr,
                "영업이익률(%)": metrics["OPM(%)"],
                "ROA(%)": metrics["ROA(%)"],
                "ROE(%)": metrics["ROE(%)"],
                "F-Score": metrics["F_Score"],
                "기준보고서": metrics["기준보고서"],
            })
        if progress_cb:
            progress_cb("fscore", i + 1, total)
        time.sleep(0.3)  # DART API 안정 호출 딜레이

    df_final = pd.DataFrame(results)

    if not df_final.empty:
        df_final = df_final[df_final["F-Score"] >= criteria["MIN_FSCORE"]].sort_values(
            by=["F-Score", "PER"], ascending=[False, True]
        )

    return df_final


# ==========================================
# 4단계: 배치 캐시 조회 (OpenDART가 해외 IP를 차단해 Streamlit Cloud에서 run_pipeline()이
# 항상 ConnectTimeout으로 실패하므로, DART 의존 데이터 수집은 로컬(한국 IP)에서 batch.py로
# 미리 실행해 data/screening_cache.csv에 저장해두고 배포된 앱은 그 캐시만 읽는다)
# ==========================================
def load_cache_meta():
    if CACHE_META.exists():
        return json.loads(CACHE_META.read_text(encoding="utf-8"))
    return None


def run_pipeline_from_cache(criteria):
    """
    batch.py가 로컬에서 미리 만들어둔 data/screening_cache.csv에서 조건에 맞는 종목만 걸러낸다.
    외부 네트워크 호출이 전혀 없으므로 OpenDART가 막혀 있는 환경에서도 즉시 동작한다.

    캐시는 BROAD_CACHE_CRITERIA로 넓게 잡은 후보군만 담고 있으므로, 여기서 그보다 느슨한
    조건(예: 최소 시가총액을 300억보다 낮게)을 걸어도 캐시에 없는 종목은 결과에 나타나지 않는다.
    """
    if not CACHE_CSV.exists():
        raise FileNotFoundError(
            "캐시 파일이 없습니다. 로컬 환경에서 `python batch.py`를 실행해 "
            "data/screening_cache.csv를 만든 뒤 커밋/푸시하세요."
        )
    df = pd.read_csv(CACHE_CSV, dtype={"종목코드": str})

    if "시장구분" not in df.columns:
        df["시장구분"] = "전체"  # 구버전 캐시(시장구분 미포함) 호환

    trend_cols = ["OPM_trend", "ROA_trend", "ROE_trend", "PBR_trend", "PER_trend"]
    financials_5y_cols = [
        "연도_5y", "매출액_5y", "경상이익_5y", "경상이익률_5y",
        "CFO_5y", "CFI_5y", "CFF_5y", "FCF_5y",
        "부채비율_5y", "당좌비율_5y", "이자보상배율_5y", "유보율_5y",
        "매출성장률_5y", "매출채권회전율_5y", "재고자산회전율_5y",
    ]
    for col in trend_cols + financials_5y_cols:
        if col not in df.columns:
            df[col] = "[]"  # 구버전 캐시(해당 컬럼 미포함) 호환
        df[col] = df[col].fillna("[]").apply(json.loads)

    market = criteria.get("MARKET", "전체")
    cond = (
        (df["시가총액"] >= criteria["MIN_MARCAP"])
        & (df["PER"] > 0) & (df["PER"] <= criteria["MAX_PER"])
        & (df["20D_Avg_Trading_Val"] >= criteria["MIN_TRADING_VALUE_20D"])
        & (df["OPM(%)"] >= criteria["MIN_OPM"])
        & (df["ROA(%)"] >= criteria["MIN_ROA"])
        & (df["ROE(%)"] >= criteria["MIN_ROE"])
        & (df["PBR"] > 0) & (df["PBR"] <= criteria["MAX_PBR"])
        & (df["F_Score"] >= criteria["MIN_FSCORE"])
    )
    if market != "전체":
        cond &= df["시장구분"] == market
    df_pass = df[cond].copy()
    df_pass["시가총액(억)"] = (df_pass["시가총액"] / 100_000_000).round().astype(int)
    df_pass["20일거래대금(억)"] = (df_pass["20D_Avg_Trading_Val"] / 100_000_000).round(1)

    df_final = df_pass.rename(columns={"OPM(%)": "영업이익률(%)", "F_Score": "F-Score"})[[
        "종목코드", "종목명", "시장구분", "시가총액(억)", "20일거래대금(억)",
        "PER", "PER_trend", "PBR", "PBR_trend",
        "영업이익률(%)", "OPM_trend", "ROA(%)", "ROA_trend", "ROE(%)", "ROE_trend",
        "F-Score", "기준보고서",
    ] + financials_5y_cols]

    return df_final.sort_values(by=["F-Score", "PER"], ascending=[False, True])


def compute_priority_scores(df, weights=None):
    """
    스크리닝 결과(df) 내에서만 상대 순위를 매겨 0~100점 종합점수(투자 우선순위)를 계산한다.

    절대값(z-score 등) 대신 종목 간 순위(percentile rank)를 합산하는 방식(Magic Formula류)이라
    한두 종목의 극단값에 전체 스케일이 휘둘리지 않는다.

    - 저평가(Value): PER·PBR이 낮을수록 높은 점수
    - 수익성(Quality): 영업이익률·ROA·ROE·F-Score가 높을수록 높은 점수
    - 개선추세(Trend): OPM/ROA/ROE 추세(과거 최대 3개 사업연도 + 현재 TTM)의 평균 증감폭이
      클수록(수익성이 개선되는 중일수록) 높은 점수
    """
    weights = weights or {"value": 1.0, "quality": 1.0, "trend": 1.0}
    total_w = sum(weights.values()) or 1.0

    def pct_rank(series, ascending):
        if len(series) <= 1:
            return pd.Series(1.0, index=series.index)
        return series.rank(pct=True, ascending=ascending)

    def trend_slope(cell):
        if not isinstance(cell, list) or len(cell) < 2:
            return 0.0
        return float(np.mean(np.diff(cell)))

    value_score = (
        pct_rank(df["PER"], ascending=False) + pct_rank(df["PBR"], ascending=False)
    ) / 2

    quality_score = (
        pct_rank(df["영업이익률(%)"], ascending=True)
        + pct_rank(df["ROA(%)"], ascending=True)
        + pct_rank(df["ROE(%)"], ascending=True)
        + pct_rank(df["F-Score"], ascending=True)
    ) / 4

    trend_raw = pd.DataFrame({
        "OPM": df["OPM_trend"].apply(trend_slope),
        "ROA": df["ROA_trend"].apply(trend_slope),
        "ROE": df["ROE_trend"].apply(trend_slope),
    }, index=df.index)
    trend_score = (
        pct_rank(trend_raw["OPM"], ascending=True)
        + pct_rank(trend_raw["ROA"], ascending=True)
        + pct_rank(trend_raw["ROE"], ascending=True)
    ) / 3

    composite = (
        value_score * weights.get("value", 1.0)
        + quality_score * weights.get("quality", 1.0)
        + trend_score * weights.get("trend", 1.0)
    ) / total_w

    return (composite * 100).round(1)

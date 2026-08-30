import re
import time
from io import StringIO

import pandas as pd
import requests
import FinanceDataReader as fdr
from opendartreader import OpenDartReader
from tqdm import tqdm

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
    df_listing = fdr.StockListing("KRX")
    df_listing = df_listing.set_index("Code")[["Name", "Marcap"]]
    df_listing.columns = ["종목명", "시가총액"]

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
REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익"]
OP_INCOME_NAMES = ["영업이익", "영업이익(손실)"]
GROSS_PROFIT_NAMES = ["매출총이익", "매출총이익(손실)"]
CFO_NAMES = ["영업활동현금흐름", "영업활동으로인한현금흐름"]


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
    """
    for name in account_names:
        matched = df[df["account_nm"].str.strip() == name]
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


def evaluate_financials_and_fscore(dart, ticker, as_of, marcap, criteria, require_per=False, max_report_attempts=4):
    """
    as_of 시점까지 공시된 가장 최근 보고서(사업/반기/분기)를 찾아 TTM(최근 12개월 합산)
    기준으로 매출·영업이익·순이익·매출총이익·영업활동현금흐름을 계산한다.

    TTM = 직전 사업연도 전체 - 전년동기누적 + 당기누적
    (분기/반기 보고서는 DART가 전년동기누적(frmtrm_add_amount)을 함께 내려주므로 추가 조회 없이
    계산 가능하고, 직전 사업연도 전체만 사업보고서를 한 번 더 조회해서 얻는다. 가장 최근 보고서가
    이미 사업보고서이면 그 값 자체가 TTM이므로 추가 조회가 필요 없다.)

    재무상태표(BS) 항목은 시점값이므로 항상 가장 최근 보고서의 당기말 값을 사용하고,
    F-Score의 전년 대비 비교는 재무상태표는 직전 사업연도말(전기), 손익 항목은 직전
    사업연도 전체(당기 대비 한 해 전 값)를 기준으로 한다.

    require_per=True이면 PER도 (marcap / TTM 당기순이익)으로 직접 계산해 MAX_PER 조건까지
    검증하고 결과에 포함한다. 백테스트처럼 특정 과거 시점의 실시간 PER 스냅샷을 구할 수
    없는 경우에 사용한다 (실시간 스크리닝은 1단계에서 Naver PER을 이미 적용했으므로 기본값 False).
    """
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

    # 1. 수익성 조건 체크 (OPM, ROA, ROE, PBR) - 전부 TTM/최신 시점 기준
    opm = (op_income_t / revenue_t * 100) if revenue_t > 0 else 0
    roa = (net_income_t / total_assets_t * 100) if total_assets_t > 0 else 0
    roe = (net_income_t / total_equity_t * 100) if total_equity_t > 0 else 0
    pbr = (marcap / total_equity_t) if total_equity_t > 0 else 0
    per = (marcap / net_income_t) if net_income_t > 0 else 0

    fail = (opm < criteria["MIN_OPM"] or
            roa < criteria["MIN_ROA"] or
            roe < criteria["MIN_ROE"] or
            not (0 < pbr <= criteria["MAX_PBR"]))
    if require_per:
        fail = fail or not (0 < per <= criteria["MAX_PER"])
    if fail:
        return None  # 조건 미달성 시 탈락

    # 2. 피오트로스키 F-Score (9개 항목) 계산
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
        "F_Score": scores,
        "기준보고서": f"{current_year}/{current_code}",
    }
    if require_per:
        result["PER"] = round(per, 2)
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

import re
import time
from io import StringIO

import pandas as pd
import requests
import FinanceDataReader as fdr
from opendartreader import OpenDartReader
from tqdm import tqdm

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

DEFAULT_TARGET_YEAR = 2024


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
# 2단계: 재무제표 파싱, 수익성(OPM/ROA/ROE) 및 F-Score 연산
# ==========================================
def clean_num(val):
    if pd.isna(val) or val == "" or val == "-":
        return 0.0
    return float(str(val).replace(",", "").strip())


def get_account_value(df, account_names, col_name):
    for name in account_names:
        matched = df[df["account_nm"].str.strip() == name]
        if not matched.empty:
            return clean_num(matched[col_name].values[0])
    return 0.0


def evaluate_financials_and_fscore(dart, ticker, bsns_year, marcap, criteria):
    try:
        # 연결재무제표(CFS) 우선, 미존재 시 개별(OFS)
        df_fs = dart.finstate_all(ticker, bsns_year, reprt_code="11011", fs_div="CFS")
        if df_fs is None or df_fs.empty:
            df_fs = dart.finstate_all(ticker, bsns_year, reprt_code="11011", fs_div="OFS")
        if df_fs is None or df_fs.empty:
            return None
    except Exception:
        return None

    col_t = "thstrm_amount"
    col_prev = "frmtrm_amount"

    bs = df_fs[df_fs["sj_div"] == "BS"]
    is_df = df_fs[df_fs["sj_div"].isin(["IS", "CIS"])]
    cf = df_fs[df_fs["sj_div"] == "CF"]

    # 계정과목 추출
    total_assets_t = get_account_value(bs, ["자산총계"], col_t)
    total_assets_prev = get_account_value(bs, ["자산총계"], col_prev)
    total_equity_t = get_account_value(bs, ["자본총계"], col_t)
    current_assets_t = get_account_value(bs, ["유동자산"], col_t)
    current_assets_prev = get_account_value(bs, ["유동자산"], col_prev)
    current_liab_t = get_account_value(bs, ["유동부채"], col_t)
    current_liab_prev = get_account_value(bs, ["유동부채"], col_prev)
    non_current_liab_t = get_account_value(bs, ["비유동부채", "장기차입금"], col_t)
    non_current_liab_prev = get_account_value(bs, ["비유동부채", "장기차입금"], col_prev)

    revenue_t = get_account_value(is_df, ["매출액", "수익(매출액)", "영업수익"], col_t)
    revenue_prev = get_account_value(is_df, ["매출액", "수익(매출액)", "영업수익"], col_prev)
    op_income_t = get_account_value(is_df, ["영업이익", "영업이익(손실)"], col_t)
    net_income_t = get_account_value(is_df, ["당기순이익", "당기순이익(손실)", "연결당기순이익"], col_t)
    net_income_prev = get_account_value(is_df, ["당기순이익", "당기순이익(손실)", "연결당기순이익"], col_prev)
    gross_profit_t = get_account_value(is_df, ["매출총이익", "매출총이익(손실)"], col_t)
    gross_profit_prev = get_account_value(is_df, ["매출총이익", "매출총이익(손실)"], col_prev)
    if gross_profit_t == 0.0:
        gross_profit_t, gross_profit_prev = op_income_t, op_income_t

    cfo_t = get_account_value(cf, ["영업활동현금흐름", "영업활동으로인한현금흐름"], col_t)

    # 1. 수익성 조건 체크 (OPM, ROA, ROE, PBR)
    opm = (op_income_t / revenue_t * 100) if revenue_t > 0 else 0
    roa = (net_income_t / total_assets_t * 100) if total_assets_t > 0 else 0
    roe = (net_income_t / total_equity_t * 100) if total_equity_t > 0 else 0
    pbr = (marcap / total_equity_t) if total_equity_t > 0 else 0

    if (opm < criteria["MIN_OPM"] or
        roa < criteria["MIN_ROA"] or
        roe < criteria["MIN_ROE"] or
        not (0 < pbr <= criteria["MAX_PBR"])):
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
    cap_t = get_account_value(bs, ["자본금"], col_t)
    cap_prev = get_account_value(bs, ["자본금"], col_prev)
    scores += 1 if cap_t <= cap_prev else 0

    # 영업효율성 (2점)
    gpm_t = (gross_profit_t / revenue_t) if revenue_t > 0 else 0
    gpm_prev = (gross_profit_prev / revenue_prev) if revenue_prev > 0 else 0
    scores += 1 if gpm_t > gpm_prev else 0

    turn_t = (revenue_t / total_assets_t) if total_assets_t > 0 else 0
    turn_prev = (revenue_prev / total_assets_prev) if total_assets_prev > 0 else 0
    scores += 1 if turn_t > turn_prev else 0

    return {
        "OPM(%)": round(opm, 2),
        "ROA(%)": round(roa, 2),
        "ROE(%)": round(roe, 2),
        "PBR": round(pbr, 2),
        "F_Score": scores
    }


# ==========================================
# 3단계: 통합 실행
# ==========================================
def run_pipeline(dart_api_key, target_year, criteria, log=print, progress_cb=None):
    """전체 스크리닝 파이프라인을 실행하고 최종 결과 DataFrame을 반환한다."""
    dart = OpenDartReader(dart_api_key)
    df_candidates = run_first_stage_screening(criteria, log=log, progress_cb=progress_cb)

    results = []
    log(f"\n▶ [2단계] 1차 통과 {len(df_candidates)}개 종목 수익성 필터 및 F-Score 검증 시작...")

    total = len(df_candidates)
    for i, ticker in enumerate(tqdm(df_candidates.index)):
        name = df_candidates.loc[ticker, "종목명"]
        marcap = df_candidates.loc[ticker, "시가총액"]
        metrics = evaluate_financials_and_fscore(dart, ticker, target_year, marcap, criteria)

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
                "F-Score": metrics["F_Score"]
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

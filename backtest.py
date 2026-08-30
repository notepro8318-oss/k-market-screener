import hashlib
import json
import pickle
import time
from pathlib import Path

import pandas as pd
import FinanceDataReader as fdr

from screener import create_dart_client, evaluate_financials_and_fscore

CACHE_DIR = Path(__file__).parent / "backtest_cache"
CACHE_DIR.mkdir(exist_ok=True)

BENCHMARKS = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}


def _cache_key(criteria, start_year, end_year, universe_size, benchmark):
    payload = json.dumps(
        {
            "criteria": criteria,
            "start_year": start_year,
            "end_year": end_year,
            "universe_size": universe_size,
            "benchmark": benchmark,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load_cache(key):
    path = CACHE_DIR / f"{key}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


def _save_cache(key, result):
    path = CACHE_DIR / f"{key}.pkl"
    with open(path, "wb") as f:
        pickle.dump(result, f)


def _rebalance_date(report_year):
    """report_year 사업보고서는 보통 다음 해 3월말까지 공시되므로 4/1을 리밸런싱일로 사용."""
    return pd.Timestamp(year=report_year + 1, month=4, day=1)


def _row_on_or_before(hist, date):
    sub = hist[hist.index <= date]
    return sub.iloc[-1] if not sub.empty else None


def run_backtest(dart_api_key, criteria, start_year, end_year, universe_size=500,
                  benchmark="KOSPI", log=print, progress_cb=None, use_cache=True):
    """
    연 1회(매년 4/1) 리밸런싱하는 과거 시뮬레이션.

    한계:
    - 대상 유니버스는 '현재' 시가총액 상위 universe_size 종목으로 제한 (상장폐지 종목 미포함 -> 생존편향 존재)
    - 발행주식수는 현재 값을 과거에도 동일하게 근사 사용
    - PER/PBR은 실시간 스냅샷이 아니라 (해당 시점 시가총액 / DART 재무제표)로 직접 계산
    """
    key = _cache_key(criteria, start_year, end_year, universe_size, benchmark)
    if use_cache:
        cached = _load_cache(key)
        if cached is not None:
            log("✔ 캐시된 백테스트 결과를 불러왔습니다.")
            return cached

    today = pd.Timestamp.today().normalize()
    hist_start = pd.Timestamp(year=start_year, month=1, day=1) - pd.Timedelta(days=60)

    log(f"▶ 백테스트 유니버스 구성 중 (현재 시가총액 상위 {universe_size}종목)...")
    df_listing = fdr.StockListing("KRX").sort_values("Marcap", ascending=False).head(universe_size)
    df_listing = df_listing.set_index("Code")
    shares_map = df_listing["Stocks"].to_dict()
    name_map = df_listing["Name"].to_dict()
    tickers = list(df_listing.index)

    log("▷ 종목별 전체 주가 이력 수집 중 (1회 수집 후 각 리밸런싱 시점에 재사용)...")
    price_hist = {}
    for i, ticker in enumerate(tickers):
        try:
            df_p = fdr.DataReader(ticker, hist_start, today)
            if not df_p.empty:
                price_hist[ticker] = df_p
        except Exception:
            pass
        if progress_cb:
            progress_cb("history", i + 1, len(tickers))

    log("▷ 벤치마크 지수 이력 수집 중...")
    bench_hist = fdr.DataReader(BENCHMARKS.get(benchmark, "KS11"), hist_start, today)

    dart = create_dart_client(dart_api_key, log=log)

    report_years = [y for y in range(start_year, end_year + 1) if _rebalance_date(y) <= today]
    yearly_rows = []
    holdings_by_year = {}

    for report_year in report_years:
        rb_date = _rebalance_date(report_year)
        next_rb_date = _rebalance_date(report_year + 1)
        is_open_period = next_rb_date > today
        exit_date = min(next_rb_date, today)

        log(f"▶ {report_year}년(사업보고서) 리밸런싱 처리 중... (기준일 {rb_date.date()})")

        # 1차 필터 (시가총액 + 20일 평균거래대금 근사) - DART 호출 없음
        candidates = []
        for ticker, hist in price_hist.items():
            row = _row_on_or_before(hist, rb_date)
            if row is None:
                continue
            marcap = row["Close"] * shares_map.get(ticker, 0)
            window = hist[hist.index <= rb_date].tail(20)
            if window.empty:
                continue
            avg_trd = (window["Close"] * window["Volume"]).mean()
            if marcap >= criteria["MIN_MARCAP"] and avg_trd >= criteria["MIN_TRADING_VALUE_20D"]:
                candidates.append((ticker, marcap))

        log(f"  1차 필터 통과: {len(candidates)}개 종목 → DART 재무데이터 조회 시작")

        selected = []
        for ci, (ticker, marcap) in enumerate(candidates):
            metrics = evaluate_financials_and_fscore(
                dart, ticker, report_year, marcap, criteria, require_per=True
            )
            if metrics is not None and metrics["F_Score"] >= criteria["MIN_FSCORE"]:
                entry_row = _row_on_or_before(price_hist[ticker], rb_date)
                exit_row = _row_on_or_before(price_hist[ticker], exit_date)
                ret = None
                if entry_row is not None and exit_row is not None and entry_row["Close"] > 0:
                    ret = round((exit_row["Close"] / entry_row["Close"] - 1) * 100, 2)
                selected.append({
                    "종목코드": ticker,
                    "종목명": name_map.get(ticker, ticker),
                    **metrics,
                    "수익률(%)": ret,
                })
            if progress_cb:
                progress_cb(f"dart_{report_year}", ci + 1, max(len(candidates), 1))
            time.sleep(0.3)

        valid_returns = [it["수익률(%)"] for it in selected if it["수익률(%)"] is not None]
        port_return = (sum(valid_returns) / len(valid_returns) / 100) if valid_returns else None

        bench_entry = _row_on_or_before(bench_hist, rb_date)
        bench_exit = _row_on_or_before(bench_hist, exit_date)
        bench_return = None
        if bench_entry is not None and bench_exit is not None and bench_entry["Close"] > 0:
            bench_return = bench_exit["Close"] / bench_entry["Close"] - 1

        yearly_rows.append({
            "사업보고서연도": report_year,
            "리밸런싱일": rb_date.date().isoformat(),
            "선정종목수": len(selected),
            "포트폴리오수익률(%)": round(port_return * 100, 2) if port_return is not None else None,
            f"{benchmark}수익률(%)": round(bench_return * 100, 2) if bench_return is not None else None,
            "초과수익률(%p)": (
                round((port_return - bench_return) * 100, 2)
                if (port_return is not None and bench_return is not None) else None
            ),
            "진행상태": "진행중(미완결)" if is_open_period else "완료",
        })
        holdings_by_year[report_year] = pd.DataFrame(selected)

    df_yearly = pd.DataFrame(yearly_rows)

    completed = df_yearly[
        (df_yearly["진행상태"] == "완료") & df_yearly["포트폴리오수익률(%)"].notna()
    ]
    equity, bench_equity = 1.0, 1.0
    curve_rows = []
    for _, row in completed.iterrows():
        equity *= (1 + row["포트폴리오수익률(%)"] / 100)
        bench_equity *= (1 + row[f"{benchmark}수익률(%)"] / 100)
        curve_rows.append({
            "사업보고서연도": row["사업보고서연도"],
            "포트폴리오": round(equity, 4),
            benchmark: round(bench_equity, 4),
        })
    df_curve = pd.DataFrame(curve_rows)

    n_years = len(completed)
    cagr = (equity ** (1 / n_years) - 1) if n_years > 0 else None
    bench_cagr = (bench_equity ** (1 / n_years) - 1) if n_years > 0 else None

    mdd = None
    if not df_curve.empty:
        running_max = df_curve["포트폴리오"].cummax()
        mdd = ((df_curve["포트폴리오"] - running_max) / running_max).min()

    result = {
        "yearly": df_yearly,
        "equity_curve": df_curve,
        "holdings_by_year": holdings_by_year,
        "cagr": cagr,
        "benchmark_cagr": bench_cagr,
        "mdd": mdd,
        "universe_size": universe_size,
        "benchmark": benchmark,
    }

    if use_cache:
        _save_cache(key, result)

    return result

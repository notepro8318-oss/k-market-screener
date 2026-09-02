"""
로컬(한국 IP)에서 실행하는 배치 스크립트.

OpenDART(opendart.fss.or.kr)는 Streamlit Community Cloud처럼 해외 IP 대역의 연결을
방화벽에서 조용히 막아버려서(ConnectTimeout), 배포된 앱이 직접 DART를 호출하는 방식으로는
스크리닝이 항상 실패한다. 그래서 DART 의존 데이터 수집은 이 스크립트로 로컬에서 미리 실행해
data/screening_cache.csv에 저장해두고, Streamlit 앱(views/screener_view.py)은 그 캐시
파일만 읽어서 조건 필터링을 한다 (screener.run_pipeline_from_cache 참고).

사용법:
    export DART_API_KEY="발급받은_키"   # PowerShell: $env:DART_API_KEY = "발급받은_키"
    export ECOS_API_KEY="발급받은_키"   # 선택: 없으면 업종 평균 회전율 없이 나머지는 그대로 수집
    python batch.py

DART 재무제표는 분기 단위로만 갱신되므로 매일 돌릴 필요는 없고, 새 분기/반기/사업보고서가
공시된 후 한 번씩(또는 주 1회) 실행해 data/ 아래 결과를 커밋·푸시하면 된다.

코스피·코스닥 상장 전 종목(PER 계산이 가능한, 즉 흑자인 종목)을 대상으로 하며, 종목당
OPM/ROA/ROE/PBR/PER의 과거 추세(최근 3개 사업연도 + 현재 TTM)와 최근 5개 사업연도의
매출액/경상이익/경상이익률/영업·투자·재무활동현금흐름/잉여현금흐름, 기업분석 체크리스트
지표(부채비율/당좌비율/이자보상배율/유보율/매출성장률/매출채권회전율/재고자산회전율)도
함께 수집한다 - 그만큼 DART 조회가 늘어나 종목 수 기준 실행 시간이 길어진다
(수천 종목 기준 1시간 이상 소요 가능).

ECOS_API_KEY가 있으면 한국은행 ECOS(경제통계시스템)에서 업종별 매출채권회전율·재고자산회전율
평균(통계표 501Y008)도 한 번만 받아와서, 종목별 업종코드(DART company API)에 매칭해 함께
저장한다 - "동종업계 대비" 판정의 기준값으로 쓰인다.
"""

import datetime
import json
import os
import sys
import time

import pandas as pd

from screener import (
    BROAD_CACHE_CRITERIA,
    CACHE_CSV,
    CACHE_META,
    DATA_DIR,
    compute_5y_financials,
    compute_raw_metrics,
    create_dart_client,
    fetch_ecos_industry_turnover,
    match_industry_turnover,
    run_first_stage_screening,
)

sys.stdout.reconfigure(encoding="utf-8")


def run_batch(dart_api_key, ecos_api_key=None, log=print):
    DATA_DIR.mkdir(exist_ok=True)
    as_of = pd.Timestamp.today()

    dart = create_dart_client(dart_api_key, log=log)
    df_candidates = run_first_stage_screening(BROAD_CACHE_CRITERIA, log=log)

    ecos_lookup = {}
    if ecos_api_key:
        try:
            ecos_lookup, _ = fetch_ecos_industry_turnover(ecos_api_key, log=log)
        except Exception as e:
            log(f"⚠ ECOS 업종 평균 회전율 조회 실패({e.__class__.__name__}) - 해당 항목 없이 진행합니다.")
    else:
        log("ℹ ECOS_API_KEY가 없어 업종 평균 회전율(동종업계 대비) 없이 진행합니다.")

    rows = []
    total = len(df_candidates)
    log(f"\n▶ [2단계] 1차 통과 {total}개 종목 재무데이터 수집 시작... (TTM 기준일: {as_of.date()})")

    for i, ticker in enumerate(df_candidates.index):
        name = df_candidates.loc[ticker, "종목명"]
        market = df_candidates.loc[ticker, "시장구분"]
        marcap = df_candidates.loc[ticker, "시가총액"]
        per = df_candidates.loc[ticker, "PER"]  # Naver PER (1차 필터에서 이미 수집됨)
        avg_trd = df_candidates.loc[ticker, "20D_Avg_Trading_Val"]

        metrics = compute_raw_metrics(dart, ticker, as_of, marcap, include_trend=True)
        if metrics is not None:
            fin5y = compute_5y_financials(dart, ticker, as_of) or {}

            industry_avg, industry_ecos_code = None, None
            if ecos_lookup:
                try:
                    induty_code = dart.company(ticker).get("induty_code")
                    industry_avg, industry_ecos_code = match_industry_turnover(induty_code, ecos_lookup)
                except Exception:
                    pass

            rows.append({
                "종목코드": ticker,
                "종목명": name,
                "시장구분": market,
                "시가총액": marcap,
                "PER": per,
                "20D_Avg_Trading_Val": avg_trd,
                "OPM(%)": metrics["OPM(%)"],
                "ROA(%)": metrics["ROA(%)"],
                "ROE(%)": metrics["ROE(%)"],
                "PBR": metrics["PBR"],
                "F_Score": metrics["F_Score"],
                "기준보고서": metrics["기준보고서"],
                "OPM_trend": json.dumps(metrics["OPM_trend"]),
                "ROA_trend": json.dumps(metrics["ROA_trend"]),
                "ROE_trend": json.dumps(metrics["ROE_trend"]),
                "PBR_trend": json.dumps(metrics["PBR_trend"]),
                "PER_trend": json.dumps(metrics["PER_trend"]),
                "연도_5y": json.dumps(fin5y.get("연도", [])),
                "매출액_5y": json.dumps(fin5y.get("매출액", [])),
                "경상이익_5y": json.dumps(fin5y.get("경상이익", [])),
                "경상이익률_5y": json.dumps(fin5y.get("경상이익률(%)", [])),
                "CFO_5y": json.dumps(fin5y.get("영업활동현금흐름", [])),
                "CFI_5y": json.dumps(fin5y.get("투자활동현금흐름", [])),
                "CFF_5y": json.dumps(fin5y.get("재무활동현금흐름", [])),
                "FCF_5y": json.dumps(fin5y.get("잉여현금흐름", [])),
                "부채비율_5y": json.dumps(fin5y.get("부채비율(%)", [])),
                "당좌비율_5y": json.dumps(fin5y.get("당좌비율(%)", [])),
                "이자보상배율_5y": json.dumps(fin5y.get("이자보상배율", [])),
                "유보율_5y": json.dumps(fin5y.get("유보율(%)", [])),
                "매출성장률_5y": json.dumps(fin5y.get("매출성장률(%)", [])),
                "매출채권회전율_5y": json.dumps(fin5y.get("매출채권회전율", [])),
                "재고자산회전율_5y": json.dumps(fin5y.get("재고자산회전율", [])),
                "업종매칭_ECOS코드": industry_ecos_code or "",
                "업종매칭명": (industry_avg or {}).get("업종명", ""),
                "매출채권회전율_업계평균": (industry_avg or {}).get("매출채권회전율"),
                "재고자산회전율_업계평균": (industry_avg or {}).get("재고자산회전율"),
            })
        if (i + 1) % 10 == 0 or (i + 1) == total:
            log(f"  진행: {i + 1}/{total}")
        time.sleep(0.3)  # DART API 안정 호출 딜레이

    df = pd.DataFrame(rows)
    df.to_csv(CACHE_CSV, index=False, encoding="utf-8-sig")
    CACHE_META.write_text(
        json.dumps(
            {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"), "count": len(df)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    log(f"\n✔ {len(df)}개 종목 캐시 저장 완료 → {CACHE_CSV}")
    log("  data/screening_cache.csv, data/screening_cache_meta.json 을 git add/commit/push 하세요.")
    return df


if __name__ == "__main__":
    key = os.environ.get("DART_API_KEY", "")
    ecos_key = os.environ.get("ECOS_API_KEY", "")
    if not key:
        print("DART_API_KEY 환경 변수가 설정되지 않았습니다.")
        print("https://opendart.fss.or.kr 에서 발급받은 키를 환경 변수로 설정한 뒤 다시 실행하세요.")
    else:
        run_batch(key, ecos_api_key=ecos_key)

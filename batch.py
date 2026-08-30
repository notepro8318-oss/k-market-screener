"""
로컬(한국 IP)에서 실행하는 배치 스크립트.

OpenDART(opendart.fss.or.kr)는 Streamlit Community Cloud처럼 해외 IP 대역의 연결을
방화벽에서 조용히 막아버려서(ConnectTimeout), 배포된 앱이 직접 DART를 호출하는 방식으로는
스크리닝이 항상 실패한다. 그래서 DART 의존 데이터 수집은 이 스크립트로 로컬에서 미리 실행해
data/screening_cache.csv에 저장해두고, Streamlit 앱(views/screener_view.py)은 그 캐시
파일만 읽어서 조건 필터링을 한다 (screener.run_pipeline_from_cache 참고).

사용법:
    export DART_API_KEY="발급받은_키"   # PowerShell: $env:DART_API_KEY = "발급받은_키"
    python batch.py

DART 재무제표는 분기 단위로만 갱신되므로 매일 돌릴 필요는 없고, 새 분기/반기/사업보고서가
공시된 후 한 번씩(또는 주 1회) 실행해 data/ 아래 결과를 커밋·푸시하면 된다.
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
    compute_raw_metrics,
    create_dart_client,
    run_first_stage_screening,
)

sys.stdout.reconfigure(encoding="utf-8")


def run_batch(dart_api_key, log=print):
    DATA_DIR.mkdir(exist_ok=True)
    as_of = pd.Timestamp.today()

    dart = create_dart_client(dart_api_key, log=log)
    df_candidates = run_first_stage_screening(BROAD_CACHE_CRITERIA, log=log)

    rows = []
    total = len(df_candidates)
    log(f"\n▶ [2단계] 1차 통과 {total}개 종목 재무데이터 수집 시작... (TTM 기준일: {as_of.date()})")

    for i, ticker in enumerate(df_candidates.index):
        name = df_candidates.loc[ticker, "종목명"]
        marcap = df_candidates.loc[ticker, "시가총액"]
        per = df_candidates.loc[ticker, "PER"]  # Naver PER (1차 필터에서 이미 수집됨)
        avg_trd = df_candidates.loc[ticker, "20D_Avg_Trading_Val"]

        metrics = compute_raw_metrics(dart, ticker, as_of, marcap)
        if metrics is not None:
            rows.append({
                "종목코드": ticker,
                "종목명": name,
                "시가총액": marcap,
                "PER": per,
                "20D_Avg_Trading_Val": avg_trd,
                "OPM(%)": metrics["OPM(%)"],
                "ROA(%)": metrics["ROA(%)"],
                "ROE(%)": metrics["ROE(%)"],
                "PBR": metrics["PBR"],
                "F_Score": metrics["F_Score"],
                "기준보고서": metrics["기준보고서"],
            })
        if (i + 1) % 20 == 0 or (i + 1) == total:
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
    if not key:
        print("DART_API_KEY 환경 변수가 설정되지 않았습니다.")
        print("https://opendart.fss.or.kr 에서 발급받은 키를 환경 변수로 설정한 뒤 다시 실행하세요.")
    else:
        run_batch(key)

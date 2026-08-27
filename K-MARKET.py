import os
import sys

from screener import DEFAULT_FILTER_CRITERIA, DEFAULT_TARGET_YEAR, run_pipeline

sys.stdout.reconfigure(encoding="utf-8")

DART_API_KEY = os.environ.get("DART_API_KEY", "")


def main():
    if not DART_API_KEY:
        print("DART_API_KEY 환경 변수가 설정되지 않았습니다.")
        print("https://opendart.fss.or.kr 에서 발급받은 키를 환경 변수로 설정한 뒤 다시 실행하세요.")
        return

    df_final = run_pipeline(DART_API_KEY, DEFAULT_TARGET_YEAR, DEFAULT_FILTER_CRITERIA)

    if not df_final.empty:
        print("\n" + "=" * 80)
        print(f"★ [최종 선별] 1차 필터링 + F-Score {DEFAULT_FILTER_CRITERIA['MIN_FSCORE']}점 이상 수익가치주 포트폴리오")
        print("=" * 80)
        print(df_final.to_string(index=False))
        df_final.to_csv("Korea_Value_HighQuality_Stocks.csv", index=False, encoding="utf-8-sig")
        print("\n결과가 'Korea_Value_HighQuality_Stocks.csv' 파일로 저장되었습니다.")
    else:
        print("\n조건을 모두 만족하는 종목이 없습니다.")


if __name__ == "__main__":
    main()

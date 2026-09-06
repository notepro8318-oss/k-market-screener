"""
로컬 전용 크롤러: WICS 10개 섹터의 YTD 일별 시가총액을 wiseindex.com에서 수집해
data/sector_mdd_cache.json에 캐시한다.

- 첫 실행(캐시 없음): 올해 1/1부터 오늘까지 영업일(월~금) 전체를 백필한다(섹터당
  약 170일 x 10섹터 = 병렬 요청으로 수 분 소요).
- 이후 실행(캐시 있음): 캐시에 없는 날짜만 증분 수집한다(매일 실행 시 1일치만 추가).
- 매일 자동 실행은 update_market_data_daily.ps1에 포함되어 있다.
"""

import sys
from datetime import date

import pandas as pd

from sector_mdd import SECTOR_CODES, crawl_sector_series, load_sector_cache, save_sector_cache


def main():
    today = date.today()
    year_start = date(today.year, 1, 1)
    all_weekdays = [d.strftime("%Y%m%d") for d in pd.bdate_range(year_start, today)]

    cache = load_sector_cache()
    total_new = 0

    for sec_cd, name in SECTOR_CODES.items():
        existing = cache.get(sec_cd, {})
        missing_dates = [d for d in all_weekdays if d not in existing]
        if not missing_dates:
            print(f"{sec_cd}({name}): 이미 최신 상태, 건너뜀")
            continue

        print(f"{sec_cd}({name}): {len(missing_dates)}일 수집 중...")
        fetched = crawl_sector_series(sec_cd, missing_dates)
        existing.update(fetched)
        cache[sec_cd] = existing
        total_new += len(fetched)
        print(f"{sec_cd}({name}): {len(fetched)}일 수집 완료 (누적 {len(existing)}일)")

    save_sector_cache(cache)
    print(f"\n총 {total_new}건 신규 수집, 캐시 저장 완료: sector_mdd_cache.json")


if __name__ == "__main__":
    sys.exit(main())

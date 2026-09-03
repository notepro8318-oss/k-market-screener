"""
로컬에서 매일(또는 필요할 때) 실행해 indexergo.com에서 코스피(전체지수) PBR 확정치를
가져와 data/kospi_pbr_cache.json에 저장하는 스크립트. 이 사이트는 Cloudflare 차단이
없어 일반 requests로 충분하다(curl_cffi 불필요).

사용법:
    python crawl_kospi_pbr.py
"""

import sys

from market_dashboard import fetch_kospi_pbr_indexergo, save_kospi_pbr_cache

sys.stdout.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    value, as_of_date = fetch_kospi_pbr_indexergo()
    if value is None:
        print("⚠ 코스피 PBR 크롤링 실패 - indexergo.com 구조가 바뀌었거나 접속이 안 될 수 있습니다.")
        print("  기존 캐시는 그대로 두었습니다 (5일 넘게 갱신 안 되면 대시보드가 직접 입력을 요구합니다).")
        sys.exit(1)
    save_kospi_pbr_cache(value, as_of_date)
    print(f"✔ 코스피 PBR = {value} ({as_of_date} 기준) 저장 완료 → data/kospi_pbr_cache.json")

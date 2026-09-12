"""
로컬에서 매일 실행해 코스피·코스닥 전 종목의 차트 조건(월봉 5개월선 연속 상회, 거래량/거래대금
급증 등)을 평가하고 data/chart_screener_cache.json에 저장하는 스크립트.

전 종목(약 2,500개)의 일봉을 매번 새로 받아야 해서 Streamlit 요청 중 실시간으로 돌리기엔
너무 느려 로컬 배치로 분리했다 (chart_screener.py 모듈 docstring 참고). "급증" 신호는
그날그날 새로 나오는 것이므로 다른 시장 지표 크롤러와 함께 매일 실행되어야 한다.

사용법:
    python crawl_chart_screener.py
"""

import sys

from chart_screener import crawl_universe, save_chart_screener_cache

sys.stdout.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    rows = crawl_universe()
    save_chart_screener_cache(rows)
    print(f"✔ {len(rows)}개 종목 캐시 저장 완료 → data/chart_screener_cache.json")

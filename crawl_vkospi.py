"""
로컬에서 매일(또는 필요할 때) 실행해 investing.com에서 VKOSPI(코스피 변동성지수) 현재가를
가져와 data/vkospi_cache.json에 저장하는 스크립트.

investing.com은 Cloudflare 봇 차단이 걸려 있어 배포 환경(Streamlit Cloud)에서 매번 직접
조회하면 막힐 위험이 크다. DART와 같은 이유로 로컬에서 미리 받아 캐시 파일로 커밋해두고,
market_dashboard.py는 그 캐시만 읽는다 - 캐시가 5일 넘게 갱신되지 않으면 대시보드가 자동으로
VIX(사용자가 제시한 조건에 이미 명시된 대체 지표)로 전환하므로, 하루이틀 못 돌려도 죽지 않는다.

TLS 지문 위장에 curl_cffi가 필요한데, 배포 앱은 이 스크립트를 절대 실행하지 않아
requirements.txt에는 넣지 않았다 (넣으면 Streamlit Cloud 빌드에서 컴파일 실패 위험만
생김). 로컬에 없다면 먼저 설치: pip install curl_cffi

사용법:
    python crawl_vkospi.py
"""

import sys

from market_dashboard import fetch_vkospi_investing, save_vkospi_cache

sys.stdout.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    value = fetch_vkospi_investing()
    if value is None:
        print("⚠ VKOSPI 크롤링 실패 - investing.com 구조가 바뀌었거나 차단됐을 수 있습니다.")
        print("  기존 캐시는 그대로 두었습니다 (5일 넘게 갱신 안 되면 대시보드가 자동으로 VIX로 대체).")
        sys.exit(1)
    save_vkospi_cache(value)
    print(f"✔ VKOSPI = {value} 저장 완료 → data/vkospi_cache.json")

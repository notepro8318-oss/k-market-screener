# K-Market Value Screener

시가총액 · PER · PBR · 수익성(OPM/ROA/ROE) · Piotroski F-Score 기반 한국 저평가 우량주 스크리너 +
연 1회 리밸런싱 백테스트.

## 데이터 소스

- 시가총액 / 주가 이력: [FinanceDataReader](https://github.com/FinanceData/FinanceDataReader) (로그인 불필요)
- PER: Naver 금융 시가총액 페이지 스크래핑 (로그인 불필요)
- 재무제표(PBR/OPM/ROA/ROE/F-Score): [OpenDART](https://opendart.fss.or.kr) API

## ⚠️ OpenDART는 해외 IP에서 접속이 막혀 있음

OpenDART(opendart.fss.or.kr)는 Streamlit Community Cloud처럼 해외(비한국) IP 대역의 연결을
방화벽에서 조용히 차단(DROP)한다. 배포된 앱이 직접 DART API를 호출하면 재시도를 몇 번을 하든
`ConnectTimeout`으로 항상 실패한다 — 코드 버그가 아니라 네트워크 레벨 차단이며, 로컬(한국 IP)
에서는 동일한 호출이 정상 동작한다.

그래서 **메인 스크리너(📈 수익가치주 찾기) 페이지는 실시간으로 DART를 호출하지 않고, 로컬에서
미리 만들어둔 캐시 파일(`data/screening_cache.csv`)만 읽어서 조건 필터링을 한다.** 상세는
아래 "데이터 캐시 갱신" 항목 참고. (🧪 백테스트 페이지는 아직 이 방식으로 전환되지 않았으므로
Streamlit Cloud에서는 여전히 실패한다 — 로컬에서만 실행 가능.)

## 로컬 실행

### 1. 설치

```bash
pip install -r requirements.txt
```

### 2. OpenDART API 키 발급

https://opendart.fss.or.kr 에서 무료로 발급받습니다.

### 3-A. 웹 앱으로 실행 (Streamlit)

```bash
streamlit run app.py
```

백테스트 페이지에서 키를 사이드바에 직접 입력하거나, `.streamlit/secrets.toml` 파일을 만들어
아래처럼 저장해두면 매번 입력할 필요가 없습니다 (`.streamlit/secrets.toml.example` 참고, 이
파일은 git에 커밋되지 않습니다).

```toml
DART_API_KEY = "발급받은_키"
```

### 3-B. CLI로 실행

```bash
export DART_API_KEY="발급받은_키"   # PowerShell: $env:DART_API_KEY = "발급받은_키"
python K-MARKET.py
```

## 데이터 캐시 갱신 (배포된 스크리너용)

DART 재무제표는 분기 단위로만 갱신되므로 매일 돌릴 필요는 없다. 새 분기/반기/사업보고서가
공시된 후 한 번씩(또는 주 1회 정도) 로컬에서 아래를 실행하고 결과를 커밋/푸시하면, Streamlit
Cloud가 자동 재배포하면서 최신 캐시를 사용하게 된다.

```bash
export DART_API_KEY="발급받은_키"   # PowerShell: $env:DART_API_KEY = "발급받은_키"
python batch.py
git add data/screening_cache.csv data/screening_cache_meta.json
git commit -m "Refresh screening cache"
git push
```

`batch.py`는 `screener.BROAD_CACHE_CRITERIA`(최소 시총 300억, 거래대금 1억 이상 등 넓은 조건)로
후보군을 잡아 그 전체의 원본 지표를 캐시에 저장한다. Streamlit UI의 조건 슬라이더가 이 floor보다
느슨하게 설정되면 캐시에 없는 종목은 결과에 나타나지 않는다.

## Streamlit Community Cloud 배포

1. 이 저장소를 GitHub에 push (`data/screening_cache.csv` 포함)
2. https://share.streamlit.io 에서 GitHub 계정으로 로그인 후 이 저장소 선택, 진입 파일은 `app.py`
3. 백테스트 페이지를 로컬 이외에서도 쓸 계획이라면 앱 설정 > Secrets 에 아래 내용 추가 (메인
   스크리너 페이지는 캐시만 읽으므로 키가 없어도 동작함)

```toml
DART_API_KEY = "발급받은_키"
```

4. Deploy

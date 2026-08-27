# K-Market Value Screener

시가총액 · PER · PBR · 수익성(OPM/ROA/ROE) · Piotroski F-Score 기반 한국 저평가 우량주 스크리너.

## 데이터 소스

- 시가총액 / 주가 이력: [FinanceDataReader](https://github.com/FinanceData/FinanceDataReader) (로그인 불필요)
- PER: Naver 금융 시가총액 페이지 스크래핑 (로그인 불필요)
- 재무제표(PBR/OPM/ROA/ROE/F-Score): [OpenDART](https://opendart.fss.or.kr) API

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

키는 앱 실행 후 사이드바에 직접 입력하거나, `.streamlit/secrets.toml` 파일을 만들어 아래처럼 저장해두면
매번 입력할 필요가 없습니다 (`.streamlit/secrets.toml.example` 참고, 이 파일은 git에 커밋되지 않습니다).

```toml
DART_API_KEY = "발급받은_키"
```

### 3-B. CLI로 실행

```bash
export DART_API_KEY="발급받은_키"   # PowerShell: $env:DART_API_KEY = "발급받은_키"
python K-MARKET.py
```

## Streamlit Community Cloud 배포

1. 이 저장소를 GitHub에 push
2. https://share.streamlit.io 에서 GitHub 계정으로 로그인 후 이 저장소 선택, 진입 파일은 `app.py`
3. 앱 설정 > Secrets 에 아래 내용 추가

```toml
DART_API_KEY = "발급받은_키"
```

4. Deploy

import streamlit as st

st.set_page_config(page_title="K-Market Value Screener", page_icon="📈", layout="wide")

market_dashboard_page = st.Page("views/market_dashboard_view.py", title="시장 진입 타이밍", icon="🚦", default=True)
korea_cycle_page = st.Page("views/korea_cycle_view.py", title="한국 경제사이클", icon="🌐")
screener_page = st.Page("views/screener_view.py", title="수익가치주", icon="📈")
growth_page = st.Page("views/growth_view.py", title="성장주", icon="🚀")
backtest_page = st.Page("views/backtest_view.py", title="백테스트", icon="🧪")

pg = st.navigation([market_dashboard_page, korea_cycle_page, screener_page, growth_page, backtest_page])
pg.run()

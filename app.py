import streamlit as st

st.set_page_config(page_title="K-Market Value Screener", page_icon="📈", layout="wide")

screener_page = st.Page("views/screener_view.py", title="수익가치주", icon="📈", default=True)
growth_page = st.Page("views/growth_view.py", title="성장주", icon="🚀")
backtest_page = st.Page("views/backtest_view.py", title="백테스트", icon="🧪")

pg = st.navigation([screener_page, growth_page, backtest_page])
pg.run()

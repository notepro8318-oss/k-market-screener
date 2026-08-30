import streamlit as st


def get_dart_api_key():
    try:
        key = st.secrets.get("DART_API_KEY", "")
    except Exception:
        key = ""
    if not key:
        key = st.sidebar.text_input(
            "OpenDART API Key",
            type="password",
            help="https://opendart.fss.or.kr 에서 무료로 발급받을 수 있습니다.",
        )
    return key

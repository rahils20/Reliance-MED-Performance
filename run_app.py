import sys
import threading
import webview
import streamlit.web.cli as stcli

def start_streamlit():
    sys.argv = [
        "streamlit",
        "run",
        "streamlit_app.py",
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port=8501"
    ]
    stcli.main()

if __name__ == '__main__':
    t = threading.Thread(target=start_streamlit, daemon=True)
    t.start()
    webview.create_window("Chembond Water Technologies - Utility Suite", "http://localhost:8501", width=1280, height=800)
    webview.start()

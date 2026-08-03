import sys
import threading
import webbrowser
import time
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
    # Start Streamlit in a background thread
    t = threading.Thread(target=start_streamlit, daemon=True)
    t.start()
    
    # Give Streamlit 2 seconds to initialize
    time.sleep(2)
    
    # Opens Streamlit locally in a clean, dedicated app window using Microsoft Edge
    webbrowser.open("http://localhost:8501")

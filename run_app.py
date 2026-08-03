import os
import sys
import time
import socket
import threading
import webbrowser
import streamlit.web.cli as stcli

def get_script_path():
    # Locates streamlit_app.py inside the PyInstaller bundle directory
    if getattr(sys, 'frozen', False):
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "streamlit_app.py")

def is_port_open(port=8501):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def start_streamlit():
    script_path = get_script_path()
    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port=8501"
    ]
    stcli.main()

if __name__ == '__main__':
    t = threading.Thread(target=start_streamlit, daemon=True)
    t.start()
    
    # Wait for Streamlit server to finish starting before launching browser
    for _ in range(30):
        if is_port_open(8501):
            break
        time.sleep(1)
        
    webbrowser.open("http://localhost:8501")

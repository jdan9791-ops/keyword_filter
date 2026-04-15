"""
키워드 필터 검색기 — macOS 전용 런처
pywebview (WKWebView) 사용 → Electron 없이 네이티브 macOS 창

실행: python3 launcher_mac.py
빌드: pyinstaller launcher_mac.spec
"""
import os
import sys
import time
import threading
import socket

# PyInstaller 번들 내부 경로 처리
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# APP_CONFIG_DIR: 사용자별 설정 저장 (~/Library/Application Support/키워드필터검색기)
CONFIG_DIR = os.path.join(
    os.path.expanduser('~'), 'Library', 'Application Support', '키워드필터검색기'
)
os.makedirs(CONFIG_DIR, exist_ok=True)

# 기본 config.json 없으면 복사
config_src = os.path.join(BASE_DIR, 'config.json')
config_dst = os.path.join(CONFIG_DIR, 'config.json')
if not os.path.exists(config_dst) and os.path.exists(config_src):
    import shutil
    shutil.copy(config_src, config_dst)

# 환경변수 설정 (keyword_filter.py가 읽음)
os.environ['APP_CONFIG_DIR'] = CONFIG_DIR
os.environ['APP_BASE_DIR'] = BASE_DIR

# keyword_filter 임포트 전에 경로 추가
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

PORT = 8766


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """서버 포트가 열릴 때까지 대기"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


def start_server():
    """FastAPI 서버를 별도 스레드에서 실행"""
    import uvicorn
    from keyword_filter import app
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning')


def main():
    # 서버 스레드 시작
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    # 서버 준비 대기
    if not _wait_for_port(PORT):
        print(f'[ERROR] 서버가 {PORT}번 포트에서 시작되지 않았습니다.')
        sys.exit(1)

    # pywebview로 macOS 네이티브 창 열기 (WKWebView — Safari 엔진)
    import webview

    window = webview.create_window(
        title='키워드 필터 검색기',
        url=f'http://127.0.0.1:{PORT}',
        width=1280,
        height=800,
        min_size=(900, 600),
        background_color='#1e1e2e',
    )
    webview.start(debug=False)


if __name__ == '__main__':
    main()

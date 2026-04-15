"""
키워드 필터 검색기 — 메인 앱 (FastAPI + 브라우저 자동 오픈)
실행: py keyword_filter.py
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ─── Config ───────────────────────────────────────────
# APP_CONFIG_DIR: Electron이 주입하는 환경변수 (AppData 경로)
# 없으면 스크립트 폴더 사용 (개발 모드)
_config_dir = Path(os.environ.get("APP_CONFIG_DIR", str(Path(__file__).parent)))
CONFIG_FILE = _config_dir / "config.json"
TEMPLATE_FILE = Path(__file__).parent / "templates" / "index.html"

DEFAULT_CONFIG = {
    "naver_client_id": "",
    "naver_client_secret": "",
    "search_urls": [],
    "kin_enabled": False,
    "keywords": ["코인사기", "리딩사기", "주식사기", "투자사기", "로맨스스캠"],
    "max_pages": 3,
    "output_path": "",
    "exclude_blog_ids": [],
    "gemini": {"api_keys": [], "model": "gemini-1.5-flash", "daily_limit_per_key": 500},
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ─── State ────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.status = "idle"
        self.logs: list[str] = []
        self.items: list[dict] = []
        self.target_date = ""
        self.stop_requested = False
        self.error_msg = ""

    def add_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.logs.append(entry)
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "logs": self.logs[-150:],
            "items": self.items,
            "target_date": self.target_date,
            "error_msg": self.error_msg,
        }


_state = AppState()
_ws_clients: list[WebSocket] = []
_search_task: Optional[asyncio.Task] = None

# ─── App ──────────────────────────────────────────────

app = FastAPI()


async def _broadcast(data: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


@app.get("/", response_class=HTMLResponse)
async def root():
    return TEMPLATE_FILE.read_text(encoding="utf-8")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    await ws.send_json(_state.to_dict())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


@app.get("/status")
def get_status():
    return _state.to_dict()


@app.get("/settings")
def get_settings():
    return load_config()


@app.patch("/settings")
async def update_settings(body: dict):
    cfg = load_config()
    cfg.update(body)
    save_config(cfg)
    return {"ok": True}


@app.get("/browse-folder")
async def browse_folder():
    """네이티브 OS 폴더 선택 다이얼로그 (비동기)"""
    import sys
    try:
        if sys.platform == "darwin":
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'set f to choose folder with prompt "출력 폴더를 선택하세요"',
                "-e", "POSIX path of f",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            path = stdout.decode().strip().rstrip("/")
        else:
            # Windows — tkinter fallback
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c",
                "import tkinter as tk; from tkinter import filedialog;"
                "root=tk.Tk(); root.withdraw();"
                "p=filedialog.askdirectory(title='출력 폴더 선택');"
                "print(p); root.destroy()",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            path = stdout.decode().strip()
        if path:
            return {"ok": True, "path": path}
        return {"ok": False, "path": ""}
    except Exception as e:
        return {"ok": False, "path": "", "error": str(e)}


@app.post("/start")
async def start_search(body: dict):
    global _search_task
    if _state.status == "running":
        return {"ok": False, "msg": "이미 실행 중"}
    target_date = body.get("target_date") or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _state.reset()
    _state.target_date = target_date
    _state.add_log(f"키워드 필터 검색 시작 — 대상 날짜: {target_date}")
    _search_task = asyncio.create_task(_run_search(target_date))
    return {"ok": True}


@app.post("/stop")
async def stop_search():
    _state.stop_requested = True
    return {"ok": True}


@app.post("/create-folders")
async def create_folders_endpoint(body: dict):
    indices = body.get("indices", [])
    cfg = load_config()
    base_path = cfg.get("output_path", "").strip()
    if not base_path:
        return {"ok": False, "msg": "출력 폴더 경로를 설정에서 입력하세요."}

    date_str = _state.target_date.replace("-", "")[2:]  # YYMMDD
    collection_date = datetime.now().strftime("%Y%m%d")  # 수집일 YYYYMMDD
    results = []
    for idx in indices:
        if 0 <= idx < len(_state.items):
            item = _state.items[idx]
            folder = _create_folder(item, base_path, date_str, collection_date)
            _state.items[idx]["folder_created"] = folder
            results.append({"idx": idx, "folder": folder})
    await _broadcast(_state.to_dict())
    return {"ok": True, "results": results}


@app.patch("/items/{idx}")
async def update_item(idx: int, body: dict):
    if 0 <= idx < len(_state.items):
        _state.items[idx].update(body)
    return {"ok": True}


# ─── 폴더 + 원고.txt 생성 ──────────────────────────────

def _detect_app_url(url: str) -> str:
    """앱스토어 URL이면 플랫폼 반환 ('android'/'ios'/''), 일반 도메인이면 빈 문자열"""
    url_lower = url.lower()
    if "play.google.com" in url_lower:
        return "android"
    if "apps.apple.com" in url_lower or "itunes.apple.com" in url_lower:
        return "ios"
    return ""


def _create_folder(item: dict, base_path: str, date_str: str, collection_date: str = "") -> str:
    """
    폴더 생성. collection_date가 있으면 상위 수집일 폴더 생성.
    구조: base_path/YYYYMMDD(수집일)/YYMMDD 사기명칭/원고.txt
    """
    base = Path(base_path)

    # 수집일 상위 폴더 (YYYYMMDD)
    if collection_date:
        base = base / collection_date
        base.mkdir(parents=True, exist_ok=True)

    company = item.get("company_name") or "미확인"
    company_korean = (item.get("company_name_korean") or "").strip()
    scam_urls = item.get("scam_site_urls") or []
    scam_types = item.get("scam_types") or []
    whois = item.get("whois_created", "")

    # 앱 URL vs 도메인 URL 분리
    app_urls: list[tuple[str, str]] = []   # (url, platform)
    domain_urls: list[str] = []
    for url in scam_urls:
        platform = _detect_app_url(url)
        if platform:
            app_urls.append((url, platform))
        else:
            domain_urls.append(url)

    is_app = bool(app_urls)

    # 폴더명: YYMMDD 키워드
    if is_app:
        folder_keyword = company
    elif domain_urls:
        folder_keyword = domain_urls[0]
    else:
        folder_keyword = company

    folder_name = f"{date_str} {folder_keyword}"
    folder = base / folder_name
    suffix = 2
    while folder.exists():
        folder = base / f"{folder_name} {suffix}"
        suffix += 1
    folder.mkdir(parents=True, exist_ok=True)

    lines = []

    if is_app:
        # ── 앱 기반 원고 ──────────────────────────────────
        # 예: SecuG-pro 앱 사기, 주식 증권사 사칭 리딩방 운영 사기, 밴드 텔레그램 주의
        first_parts = [f"{company} 앱 사기"] + list(scam_types)
        lines.append(", ".join(first_parts))
        lines.append("관련 URL : ")
        for url, platform in app_urls:
            if platform == "android":
                lines.append(f"*{url} ( 갤럭시폰 (안드로이드) 어플 설치 경로)")
            else:
                lines.append(f"*{url} (아이폰 (iOS) 어플 설치 경로)")
    else:
        # ── 도메인 기반 원고 ──────────────────────────────
        # 예: Diamondminer 사기 다이아몬드마니어 주식 리딩방 투자 사기 diamondminer-ktre.com
        first_parts = [company, "사기"]
        if company_korean and company_korean != company:
            first_parts.append(company_korean)
        first_parts.extend(scam_types)
        if domain_urls:
            first_parts.append(domain_urls[0])
        lines.append(" ".join(first_parts))

        if domain_urls:
            lines.append("관련 URL : ")
            for domain in domain_urls:
                if whois and whois != "조회 실패":
                    lines.append(f"*{domain} ({whois} 만들어진 사이트 접속 주소)")
                else:
                    lines.append(f"*{domain}")

    (folder / "원고.txt").write_text("\n".join(lines), encoding="utf-8")
    return str(folder)


# ─── 검색 실행 ────────────────────────────────────────

async def _run_search(target_date: str):
    from scraper import search_and_analyze
    _state.status = "running"
    await _broadcast(_state.to_dict())
    try:
        cfg = load_config()
        await search_and_analyze(_state, cfg, target_date, _broadcast)
        if _state.stop_requested:
            _state.status = "idle"
            _state.add_log("중단됨")
        else:
            _state.status = "review"
            _state.add_log(f"수집 완료. {len(_state.items)}개 업체 발견. 선택 대기 중...")
    except asyncio.CancelledError:
        _state.status = "idle"
    except Exception as e:
        _state.status = "error"
        _state.error_msg = str(e)
        _state.add_log(f"⚠ 오류: {e}")
    await _broadcast(_state.to_dict())


# ─── 실행 진입점 ──────────────────────────────────────

def main():
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="warning", ws_ping_interval=None)


if __name__ == "__main__":
    main()

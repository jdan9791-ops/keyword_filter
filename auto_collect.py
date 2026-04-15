"""
키워드 필터 검색기 — 자동 수집 (헤드리스)
매일 새벽 1시에 launchd로 실행.
전날 게시글을 수집하고, 신규 건만 폴더 + 원고.txt 자동 생성.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CONFIG_DIR = os.path.join(
    os.path.expanduser('~'), 'Library', 'Application Support', '키워드필터검색기'
)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.environ['APP_CONFIG_DIR'] = CONFIG_DIR

LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'auto_collect.log')


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def load_config() -> dict:
    config_file = os.path.join(CONFIG_DIR, 'config.json')
    if os.path.exists(config_file):
        with open(config_file, encoding='utf-8') as f:
            return json.load(f)
    return {}


class HeadlessState:
    """AppState 호환 — 브로드캐스트 없는 단순 상태"""
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
        log(msg)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "logs": self.logs[-150:],
            "items": self.items,
            "target_date": self.target_date,
            "error_msg": self.error_msg,
        }


def _detect_app_url(url: str) -> str:
    url_lower = url.lower()
    if "play.google.com" in url_lower:
        return "android"
    if "apps.apple.com" in url_lower or "itunes.apple.com" in url_lower:
        return "ios"
    return ""


def create_folder(item: dict, base_path: str, date_str: str, collection_date: str) -> str:
    """폴더 + 원고.txt 생성"""
    base = Path(base_path) / collection_date
    base.mkdir(parents=True, exist_ok=True)

    company = item.get("company_name") or "미확인"
    company_korean = (item.get("company_name_korean") or "").strip()
    scam_urls = item.get("scam_site_urls") or []
    scam_types = item.get("scam_types") or []
    whois = item.get("whois_created", "")

    app_urls: list[tuple[str, str]] = []
    domain_urls: list[str] = []
    for url in scam_urls:
        platform = _detect_app_url(url)
        if platform:
            app_urls.append((url, platform))
        else:
            domain_urls.append(url)

    is_app = bool(app_urls)

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
        first_parts = [f"{company} 앱 사기"] + list(scam_types)
        lines.append(", ".join(first_parts))
        lines.append("관련 URL : ")
        for url, platform in app_urls:
            if platform == "android":
                lines.append(f"*{url} ( 갤럭시폰 (안드로이드) 어플 설치 경로)")
            else:
                lines.append(f"*{url} (아이폰 (iOS) 어플 설치 경로)")
    else:
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


async def main():
    log("=" * 50)
    log("자동 수집 시작")

    cfg = load_config()
    if not cfg:
        log("⚠ config.json이 없습니다. 종료.")
        sys.exit(1)

    base_path = cfg.get("output_path", "").strip()
    if not base_path:
        log("⚠ 출력 폴더 경로가 설정되지 않았습니다. 종료.")
        sys.exit(1)

    # 전날 날짜 계산
    yesterday = datetime.now() - timedelta(days=1)
    target_date = yesterday.strftime("%Y-%m-%d")
    date_str = yesterday.strftime("%y%m%d")  # YYMMDD (폴더명용)
    collection_date = datetime.now().strftime("%Y%m%d")  # 수집일 YYYYMMDD

    log(f"대상 날짜: {target_date}")
    log(f"수집일 폴더: {collection_date}")
    log(f"출력 경로: {base_path}/{collection_date}/")

    # 수집 실행
    state = HeadlessState()
    state.target_date = target_date

    from scraper import search_and_analyze

    async def noop_broadcast(data):
        pass

    try:
        await search_and_analyze(state, cfg, target_date, noop_broadcast)
    except Exception as e:
        log(f"⚠ 수집 중 오류: {e}")
        sys.exit(1)

    if not state.items:
        log("신규 수집 건 없음. 종료.")
        return

    log(f"신규 {len(state.items)}건 발견. 폴더 생성 시작...")

    # 모든 항목 폴더 자동 생성
    created = 0
    for item in state.items:
        try:
            folder = create_folder(item, base_path, date_str, collection_date)
            log(f"  → 폴더 생성: {folder}")
            created += 1
        except Exception as e:
            log(f"  ⚠ 폴더 생성 실패: {e}")

    log(f"자동 수집 완료. {created}개 폴더 생성됨.")
    log("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())

"""
키워드 필터 검색기 — 자동 수집 (헤드리스)
매일 새벽 1시에 launchd로 실행.
경쟁 법률사무소 5개 법인의 전날 게시글 수집 → 폴더 + 원고.txt 자동 생성 → 텔레그램 리포트.
"""
import asyncio
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# ─── 텔레그램 설정 ──────────────────────────────────
_TG_BOT_TOKEN = "8612780129:AAG5mOr0Pi-78V1oNhSNBxwj5QjK88-Muls"
_TG_CHAT_ID = -1003886677364
_TG_THREAD_ID = 2  # 블로그 필터_keyword_filter 토픽


def send_telegram(text: str):
    """텔레그램 토픽 알림 발송"""
    try:
        url = f"https://api.telegram.org/bot{_TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": _TG_CHAT_ID,
            "message_thread_id": _TG_THREAD_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"⚠ 텔레그램 발송 실패: {e}")


# ─── 경로 설정 ──────────────────────────────────────
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

    post_title = item.get("post_title") or item.get("company_name") or "미확인"
    company = item.get("company_name") or "미확인"
    scam_urls = item.get("scam_site_urls") or []
    whois = item.get("whois_created", "")

    app_urls: list[tuple[str, str]] = []
    domain_urls: list[str] = []
    for url in scam_urls:
        platform = _detect_app_url(url)
        if platform:
            app_urls.append((url, platform))
        else:
            domain_urls.append(url)

    # 폴더명: 도메인 > 회사명 순으로
    if domain_urls:
        folder_keyword = domain_urls[0]
    elif app_urls:
        folder_keyword = company
    else:
        folder_keyword = company

    folder_name = f"{date_str} {folder_keyword}"
    folder = base / folder_name
    suffix = 2
    while folder.exists():
        folder = base / f"{folder_name} {suffix}"
        suffix += 1
    folder.mkdir(parents=True, exist_ok=True)

    # 원고.txt — 새 양식
    lines = []

    # 첫 줄: 블로그 게시글 제목
    lines.append(post_title)

    # 관련 URL 섹션
    if domain_urls or app_urls:
        lines.append("관련 URL : ")
        for url, platform in app_urls:
            if platform == "android":
                lines.append(f"*{url} ( 갤럭시폰 (안드로이드) 어플 설치 경로)")
            else:
                lines.append(f"*{url} (아이폰 (iOS) 어플 설치 경로)")
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
    date_str = yesterday.strftime("%y%m%d")        # YYMMDD (폴더명용)
    collection_date = datetime.now().strftime("%Y%m%d")  # 수집일 YYYYMMDD

    log(f"대상 날짜: {target_date}")
    log(f"수집일 폴더: {collection_date}")
    log(f"출력 경로: {base_path}/{collection_date}/")

    # 모니터링 대상 5개 법인
    firms = cfg.get("competitor_firms", [
        "DAY 법률사무소",
        "법무법인 나란",
        "법률사무소 초율",
        "법무법인 대연",
        "법무법인 신결",
    ])

    from scraper import search_and_analyze

    async def noop_broadcast(data):
        pass

    firm_stats: dict[str, dict] = {}
    total_created = 0

    for firm in firms:
        log(f"\n[{firm}] 수집 시작...")

        state = HeadlessState()
        state.target_date = target_date

        # 이 법인만 블로그 검색하도록 config 조정
        firm_cfg = dict(cfg)
        firm_cfg["search_urls"] = [
            f"https://search.naver.com/search.naver?where=blog&query={urllib.parse.quote_plus(firm)}"
        ]
        firm_cfg["kin_enabled"] = False

        try:
            await search_and_analyze(state, firm_cfg, target_date, noop_broadcast)
        except Exception as e:
            log(f"  ⚠ 수집 오류: {e}")
            firm_stats[firm] = {"posts": 0, "blog_ids": 0}
            continue

        # 블로그 ID 집계
        blog_ids: set[str] = set()
        for item in state.items:
            url = item.get("post_url", "")
            if "blog.naver.com" in url:
                bid = url.split("blog.naver.com/")[-1].split("/")[0]
                blog_ids.add(bid)

        firm_stats[firm] = {
            "posts": len(state.items),
            "blog_ids": len(blog_ids),
        }
        log(f"  → {len(state.items)}개 수집 / 블로그 ID {len(blog_ids)}개")

        # 폴더 생성
        created = 0
        for item in state.items:
            try:
                folder = create_folder(item, base_path, date_str, collection_date)
                log(f"  → 폴더 생성: {folder}")
                created += 1
            except Exception as e:
                log(f"  ⚠ 폴더 생성 실패: {e}")
        total_created += created

    log(f"\n자동 수집 완료. 총 {total_created}개 폴더 생성됨.")
    log("=" * 50)

    # ─── 리포트 생성 & 텔레그램 전송 ───────────────────
    total_posts = sum(s["posts"] for s in firm_stats.values())
    total_ids = sum(s["blog_ids"] for s in firm_stats.values())

    # 테이블 구성
    rows = []
    for firm, s in firm_stats.items():
        rows.append(f"{firm:<14} {s['posts']:>5}개  {s['blog_ids']:>4}개")

    table = "\n".join(rows)
    divider = "─" * 30

    report = (
        f"📊 <b>경쟁사 블로그 모니터링 리포트</b>\n"
        f"📅 수집 기준일: {target_date}\n"
        f"📁 폴더 생성: {total_created}개\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"<pre>"
        f"{'법인명':<14} {'게시글':>5}   {'블로그':>4}\n"
        f"{divider}\n"
        f"{table}\n"
        f"{divider}\n"
        f"{'합계':<14} {total_posts:>5}개  {total_ids:>4}개"
        f"</pre>"
    )

    send_telegram(report)
    log("텔레그램 리포트 전송 완료")


if __name__ == "__main__":
    asyncio.run(main())

"""
네이버 검색 API 기반 사기 게시글 수집 + Gemini 분석 + WHOIS 조회.
"""
import asyncio
import json
import os
import re
import httpx
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Callable

# ─── 수집 이력 DB ────────────────────────────────────
_config_dir = Path(os.environ.get("APP_CONFIG_DIR", str(Path(__file__).parent)))
HISTORY_FILE = _config_dir / "history.json"


def _load_history() -> set:
    """이전에 수집된 게시글 URL 목록 로드"""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("urls", []))
        except Exception:
            pass
    return set()


def _save_history(urls: set):
    """수집된 게시글 URL 목록 저장"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"urls": list(urls)}, f, ensure_ascii=False)

NAVER_API_BASE = "https://openapi.naver.com/v1/search"

TAB_TO_ENDPOINT = {
    "blog": "blog.json",
    "view": "blog.json",
    "cafe": "cafearticle.json",
    "cafearticle": "cafearticle.json",
    "kin": "kin.json",
}

_SCAM_PATTERNS = [
    r"사기", r"피해", r"먹튀", r"스캠", r"scam",
    r"리딩방", r"리딩사기", r"코인사기", r"주식사기", r"투자사기",
    r"로맨스", r"HTS", r"MTS", r"환급", r"불법", r"신고",
    r"\d+만원.*피해", r"피해.*\d+만원",
    r"https?://", r"\.com", r"\.net", r"\.io",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _SCAM_PATTERNS]


def _passes_scam_filter(title: str, desc: str) -> bool:
    text = f"{title} {desc}"
    return any(p.search(text) for p in _COMPILED)


def _detect_tab(url: str) -> str:
    try:
        params = parse_qs(urlparse(url).query)
        where = params.get("where", [""])[0].lower()
        tab_map = {"blog": "blog", "view": "view", "cafearticle": "cafe", "cafe": "cafe", "kin": "kin"}
        if where in tab_map:
            return tab_map[where]
        ssc = params.get("ssc", [""])[0].lower()
        for key in ["blog", "view", "cafe", "kin"]:
            if key in ssc:
                return key
    except Exception:
        pass
    return "blog"


def _extract_query(url: str) -> str:
    m = re.search(r"[?&]query=([^&]+)", url)
    return m.group(1).replace("+", " ") if m else ""


def _extract_blog_id(url: str) -> str:
    m = re.search(r"blog\.naver\.com/([^/?#]+)", url)
    return m.group(1).lower() if m else ""


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


async def _call_naver_api(
    query: str, tab: str, client_id: str, client_secret: str,
    display: int = 100, start: int = 1, log_fn=None
) -> list[dict]:
    endpoint = TAB_TO_ENDPOINT.get(tab, "blog.json")
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": display, "start": start, "sort": "date"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{NAVER_API_BASE}/{endpoint}", params=params, headers=headers)
        if resp.status_code != 200:
            if log_fn:
                log_fn(f"  ⚠ API 오류 {resp.status_code}: {resp.text[:120]}")
            return []
        return resp.json().get("items", [])
    except Exception as e:
        if log_fn:
            log_fn(f"  ⚠ API 요청 실패: {e}")
        return []


def _process_scam_url(url: str) -> str:
    """URL 정리: 앱스토어는 전체 경로 유지, 일반 도메인은 도메인만 추출"""
    url = url.strip()
    if not url:
        return ""
    url_lower = url.lower()
    if any(store in url_lower for store in ["play.google.com", "apps.apple.com", "itunes.apple.com"]):
        return re.sub(r"https?://", "", url)
    return re.sub(r"https?://", "", url).split("/")[0].lstrip("www.")


async def _analyze_with_gemini(title: str, description: str, gemini_cfg: dict) -> dict:
    api_keys = gemini_cfg.get("api_keys", [])
    if not api_keys:
        return {}

    key_entry = api_keys[0]
    api_key = key_entry if isinstance(key_entry, str) else key_entry.get("api_key", "")
    model_name = gemini_cfg.get("model", "gemini-1.5-flash")

    prompt = (
        "다음 네이버 검색 결과를 분석하여 JSON만 출력하세요.\n\n"
        f"게시글 제목: {title}\n"
        f"게시글 미리보기: {description}\n\n"
        "【중요】 먼저 이 글이 '특정 업체/사이트/앱에 의한 사기 피해 사례'인지 판별하세요.\n"
        "다음에 해당하면 is_scam_case: false로 판정하세요:\n"
        "- 개인회생, 개인파산, 채무 상담 등 단순 법률 질문\n"
        "- 주식/코인/해외선물 투자 방법, 초보 질문, 계좌 개설 등 일반 투자 질문\n"
        "- ETF, 펀드, 재테크 등 일반 금융 질문\n"
        "- 사기와 무관한 일상 질문 (영어, 역사, 취미, 연애 등)\n"
        "- 특정 사기 업체명이나 사기 사이트 URL이 없는 막연한 질문\n"
        "- 변호사 추천/비용 문의만 있고 구체적 사기 업체 정보가 없는 글\n\n"
        "추출 항목:\n"
        "0. is_scam_case: 특정 업체/사이트의 사기 피해 사례이면 true, 아니면 false\n"
        "1. company_name: 사기 업체/플랫폼 이름 (영문 또는 브랜드명, 핵심 이름 하나만)\n"
        "2. company_name_korean: 사기 업체/플랫폼 한국어 이름 (있을 경우만, 없으면 빈 문자열)\n"
        "3. scam_site_urls: 사기 관련 URL 목록 (도메인 또는 앱스토어 전체 URL 포함, 배열)\n"
        "4. scam_types: 사기 유형 배열 (예: [\"코인사기\", \"리딩사기\"])\n"
        "5. summary_lines: 피해 경위 요약 3~5줄 배열\n\n"
        "출력 형식 (JSON만, 다른 텍스트 금지):\n"
        "{\"is_scam_case\": true, \"company_name\": \"\", \"company_name_korean\": \"\", \"scam_site_urls\": [], \"scam_types\": [], \"summary_lines\": []}"
    )

    try:
        import google.generativeai as genai

        def _call():
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
            return model.generate_content(prompt).text

        text = await asyncio.to_thread(_call)
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {}


async def _whois_lookup(domain: str) -> str:
    domain = re.sub(r"https?://", "", domain).split("/")[0].lstrip("www.")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"https://domain.whois.co.kr/whois/whois.asp?domain={domain}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
        for pat in [
            r"(?:Registered Date|Registration Date|Created Date)[:\s]*([\d]{4}[-./][\d]{2}[-./][\d]{2})",
            r"(?:등록일)[:\s]*([\d]{4}[-./][\d]{2}[-./][\d]{2})",
        ]:
            m = re.search(pat, resp.text, re.IGNORECASE)
            if m:
                return re.sub(r"[./]", "-", m.group(1))[:10]
    except Exception:
        pass
    try:
        import whois
        w = await asyncio.to_thread(whois.whois, domain)
        if w.creation_date:
            d = w.creation_date
            if isinstance(d, list):
                d = d[0]
            if hasattr(d, "strftime"):
                return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return "조회 실패"


async def search_and_analyze(state, cfg: dict, target_date: str, broadcast_fn: Callable):
    client_id = cfg.get("naver_client_id", "")
    client_secret = cfg.get("naver_client_secret", "")
    if not client_id or not client_secret:
        state.status = "error"
        state.error_msg = "네이버 API 키가 설정되지 않았습니다."
        state.add_log("⚠ 설정에서 네이버 Client ID/Secret을 입력하세요.")
        return

    gemini_cfg = cfg.get("gemini", {})
    exclude_ids = set(cfg.get("exclude_blog_ids", []))
    target_date_str = target_date.replace("-", "")  # YYYYMMDD

    # 검색 URL 파싱
    raw_urls = cfg.get("search_urls", [])
    search_urls = [
        (item["url"] if isinstance(item, dict) else item)
        for item in raw_urls
        if isinstance(item, str) or item.get("enabled", True)
    ]

    kin_enabled = cfg.get("kin_enabled", False)
    keywords = cfg.get("keywords", [])

    if not search_urls and not (kin_enabled and keywords):
        state.status = "error"
        state.error_msg = "검색 URL 또는 키워드가 설정되지 않았습니다."
        state.add_log("⚠ 설정에서 검색 URL을 추가하세요.")
        return

    # 검색 작업 목록 구성
    search_tasks = []  # (label, query, tab)
    for url in search_urls:
        query = _extract_query(url)
        tab = _detect_tab(url)
        if query:
            search_tasks.append((f"URL [{tab}] \"{query}\"", query, tab))
        else:
            state.add_log(f"  ⚠ query= 없는 URL 건너뜀: {url[:60]}")

    if kin_enabled and keywords:
        for kw in keywords:
            search_tasks.append((f"지식인 \"{kw}\"", kw, "kin"))

    state.add_log(f"검색 작업 {len(search_tasks)}개 시작")
    await broadcast_fn(state.to_dict())

    # 수집 이력 로드 (이전에 수집된 URL은 제외)
    history_urls = _load_history()
    state.add_log(f"수집 이력: {len(history_urls)}건 로드됨")

    # Phase A: API 호출 + 날짜 필터
    all_posts = []
    seen_urls: set = set()

    for label, query, tab in search_tasks:
        if state.stop_requested:
            break

        state.add_log(f"{label} 검색 중...")
        await broadcast_fn(state.to_dict())

        items = await _call_naver_api(
            query, tab, client_id, client_secret,
            log_fn=lambda msg: state.add_log(msg)
        )
        state.add_log(f"  → API 응답 {len(items)}건")

        date_filtered = []
        for item in items:
            postdate = item.get("postdate", "")
            # kin API는 postdate가 비어있고 pubDate(RFC 2822)로 날짜를 줌
            if not postdate and tab == "kin":
                pub_date = item.get("pubDate", "")
                if pub_date:
                    try:
                        from email.utils import parsedate
                        t = parsedate(pub_date)
                        if t:
                            postdate = f"{t[0]}{t[1]:02d}{t[2]:02d}"
                    except Exception:
                        pass
            if postdate == target_date_str:
                date_filtered.append(item)

        state.add_log(f"  → 날짜({target_date}) 필터 후 {len(date_filtered)}건")

        for item in date_filtered:
            link = item.get("link", "")
            if not link or link in seen_urls:
                continue
            if link in history_urls:
                continue  # 이전에 이미 수집된 글 제외
            if _extract_blog_id(link) in exclude_ids:
                continue
            seen_urls.add(link)
            all_posts.append({
                "url": link,
                "title": _clean_html(item.get("title", "")),
                "description": _clean_html(item.get("description", "")),
                "post_date": item.get("postdate", ""),
                "tab": tab,
            })

        await asyncio.sleep(0.1)

    state.add_log(f"1차 수집 완료: {len(all_posts)}건")
    await broadcast_fn(state.to_dict())

    # Phase B: 스캠 키워드 필터 (설정에서 비활성화 가능)
    scam_filter = cfg.get("scam_filter_enabled", False)
    if scam_filter:
        filtered = [p for p in all_posts if _passes_scam_filter(p["title"], p["description"])]
        state.add_log(f"스캠 필터 통과: {len(filtered)}건 / {len(all_posts)}건")
    else:
        filtered = all_posts
        state.add_log(f"스캠 필터 비활성화 — 전체 {len(filtered)}건 분석")
    await broadcast_fn(state.to_dict())

    if not filtered:
        return

    # Phase C: Gemini 분석 + WHOIS
    seen_companies: dict[str, int] = {}
    _gemini_interval = 4.5  # 무료 한도: 분당 15회 → 4초 간격 (여유 포함)
    _last_gemini_call = 0.0

    for i, post in enumerate(filtered):
        if state.stop_requested:
            break

        state.add_log(f"분석 중 ({i+1}/{len(filtered)}): {post['title'][:40]}")
        await broadcast_fn(state.to_dict())

        ai = {}
        if gemini_cfg.get("api_keys"):
            # 무료 한도 속도 제한 (분당 15회)
            import time
            elapsed = time.time() - _last_gemini_call
            if elapsed < _gemini_interval:
                await asyncio.sleep(_gemini_interval - elapsed)
            _last_gemini_call = time.time()
            ai = await _analyze_with_gemini(post["title"], post["description"], gemini_cfg)

        # 사기 사례가 아닌 글은 건너뛰기
        if not ai.get("is_scam_case", True):
            state.add_log(f"  → 사기 무관 글 제외")
            continue

        company_name = (ai.get("company_name") or "").strip() or post["title"][:20]
        company_name_korean = (ai.get("company_name_korean") or "").strip()
        scam_site_urls = [
            _process_scam_url(u)
            for u in ai.get("scam_site_urls", []) if u.strip()
        ]
        scam_site_urls = [u for u in scam_site_urls if u]
        scam_types = ai.get("scam_types", [])
        scam_summary = "\n".join(ai.get("summary_lines", []))

        company_key = company_name.lower()
        if company_key in seen_companies:
            idx = seen_companies[company_key]
            state.items[idx].setdefault("related_posts", []).append(post["url"])
            state.add_log(f"  → 중복 업체 '{company_name}' — 참고 URL로 추가")
            continue

        item = {
            "post_url": post["url"],
            "post_title": post["title"],
            "post_date": post["post_date"],
            "search_tab": post["tab"],
            "company_name": company_name,
            "company_name_korean": company_name_korean,
            "scam_site_url": scam_site_urls[0] if scam_site_urls else "",
            "scam_site_urls": scam_site_urls,
            "scam_types": scam_types,
            "scam_summary": scam_summary,
            "whois_created": "",
            "related_posts": [],
            "status": "analyzing",
            "selected": True,
        }
        seen_companies[company_key] = len(state.items)
        state.items.append(item)
        await broadcast_fn(state.to_dict())

        # WHOIS
        if scam_site_urls:
            state.add_log(f"  WHOIS 조회: {scam_site_urls[0]}")
            item["whois_created"] = await _whois_lookup(scam_site_urls[0])
            state.add_log(f"  → {item['whois_created']}")

        item["status"] = "done"
        await broadcast_fn(state.to_dict())

    # 수집 이력 업데이트 (이번에 수집된 URL 추가)
    history_urls.update(seen_urls)
    _save_history(history_urls)
    state.add_log(f"수집 이력 업데이트: 총 {len(history_urls)}건")

    state.add_log(f"분석 완료: {len(state.items)}개 업체 발견")

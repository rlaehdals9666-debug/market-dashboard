"""
네이버 증권 뉴스 → 새 기사만 골라 → 본문 AI 요약(Gemini 무료) → 텔레그램 전송

시간표(한국 시간, cron은 매시 정각에 이 파일을 실행하고, 할 일은 이 파일이 시간 보고 정해요)
- 평일 07:00 ~ 20:00 매시 정각 : 새 기사를 바로 요약해서 보내요.
- 평일 06:00               : 밤사이(전날 20시 이후) 모아 둔 기사를 한 번에 보내요.
                             월요일 06시에는 금요일 밤 ~ 주말 기사까지 포함돼요.
- 그 외(밤, 주말)            : 보내지 않고 새 기사를 모아만 둬요.

- 이미 본 기사는 ~/dashboard_data/news_seen.json, 모아 둔 기사는 news_queue.json 에 기록해요.
- 비밀 값(텔레그램 토큰, Gemini 키·모델)은 ~/.streamlit/secrets.toml 에서 읽어요. 이 파일에는 없어요.

실행:
  python news_bot.py              # 시간 보고 알아서 (cron용)
  python news_bot.py --now        # 지금 바로 '새 기사 보내기' (테스트용)
  python news_bot.py --digest     # 지금 바로 '모아 둔 기사 묶음 보내기' (테스트용)
"""
import html
import json
import re
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
HOME = Path.home()
SECRETS = HOME / ".streamlit" / "secrets.toml"
DATA_DIR = HOME / "dashboard_data"
SEEN_FILE = DATA_DIR / "news_seen.json"
QUEUE_FILE = DATA_DIR / "news_queue.json"

SECTION = ("101", "258")          # 네이버 뉴스 경제 > 증권
LIST_LIMIT = 30                   # 목록에서 한 번에 보는 기사 수
MAX_PER_RUN = 15                  # 낮 시간 한 번에 자세히 요약하는 최대 기사 수
DIGEST_DETAIL = 20                # 06시 묶음에서 자세히 요약하는 기사 수 (나머지는 제목 목록)
QUEUE_MAX = 300                   # 모아 두는 최대 기사 수
FIRST_RUN_SEND = 5                # 기록 파일이 없을 때(처음)는 최신 5건만 보내기
BODY_CHARS = 3000                 # 요약에 넘기는 본문 길이(글자)
LIVE_HOURS = range(7, 21)         # 07:00 ~ 20:59 → 바로 보내기
DIGEST_HOUR = 6                   # 06시 → 밤사이 묶음
WEEKDAYS = range(0, 5)            # 월~금

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
# 2026-11 확인: flash-lite 계열이 무료 등급에서 빠르고 안정적. 앞에서부터 차례로 시도해요.
GEMINI_MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-flash-latest"]


# ---------------------------------------------------------------------
# 설정·기록
# ---------------------------------------------------------------------
def load_secrets() -> dict:
    with open(SECRETS, "rb") as f:
        s = tomllib.load(f)
    tg = s.get("telegram", {})
    gm = s.get("gemini", {})
    if not tg.get("token") or not tg.get("chat_id"):
        sys.exit("secrets.toml 에 [telegram] token / chat_id 가 없어요.")
    return {"tg_token": tg["token"], "tg_chat": str(tg["chat_id"]),
            "gemini_key": gm.get("api_key", ""), "gemini_model": gm.get("model", "")}


def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------
# 네이버 뉴스 목록 (app.py 의 parse_naver_news 와 같은 방식)
# ---------------------------------------------------------------------
def _strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def parse_naver_news(doc: str, limit: int) -> list:
    items, seen = [], set()
    for block in re.split(r'<li[^>]*class="[^"]*sa_item', doc)[1:]:
        tag = re.search(r'<a\b[^>]*sa_text_title[^>]*>', block)
        inner = re.search(r'<a\b[^>]*sa_text_title[^>]*>(.*?)</a>', block, re.S)
        href = re.search(r'href="([^"]+)"', tag.group(0)) if tag else None
        if not (tag and inner and href):
            continue
        title = _strip_tags(inner.group(1))
        if len(title) < 6 or title in seen:
            continue
        seen.add(title)
        press = re.search(r'sa_text_press[^>]*>([^<]+)<', block)
        when = re.search(r'sa_text_datetime[^>]*>\s*<b[^>]*>([^<]+)<', block)
        items.append({"title": title, "link": html.unescape(href.group(1)),
                      "source": press.group(1).strip() if press else "네이버뉴스",
                      "time_text": when.group(1).strip() if when else ""})
        if len(items) >= limit:
            return items
    if items:
        return items
    for m in re.finditer(r'<a\b[^>]*href="([^"]*(?:/article/|read\.naver)[^"]*)"[^>]*>(.*?)</a>', doc, re.S):
        title = _strip_tags(m.group(2))
        if len(title) < 10 or title in seen:
            continue
        seen.add(title)
        link = html.unescape(m.group(1))
        items.append({"title": title, "link": ("https://news.naver.com" + link) if link.startswith("/") else link,
                      "source": "네이버뉴스", "time_text": ""})
        if len(items) >= limit:
            break
    return items


def get_section_news(limit: int = LIST_LIMIT) -> list:
    sid1, sid2 = SECTION
    for url in (f"https://news.naver.com/breakingnews/section/{sid1}/{sid2}",
                f"https://news.naver.com/main/list.naver?mode=LSD&mid=sec&sid1={sid1}&sid2={sid2}"):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log(f"목록 실패 {url}: {e}")
            continue
        items = parse_naver_news(r.text, limit)
        if items:
            return items
    return []


def article_key(link: str) -> str:
    """같은 기사를 한 번만 다루도록 링크에서 (언론사번호/기사번호)를 뽑아요."""
    m = re.search(r"/article/(\d+)/(\d+)", link) or re.search(r"oid=(\d+).*?aid=(\d+)", link)
    return f"{m.group(1)}/{m.group(2)}" if m else link


def get_body(link: str) -> str:
    doc = ""
    for attempt in range(2):
        try:
            r = requests.get(link, headers=HEADERS, timeout=15)
            r.raise_for_status()
            doc = r.text
            break
        except Exception as e:
            log(f"본문 실패({attempt + 1}) {link}: {e}")
            time.sleep(2)
    if not doc:
        return ""
    m = re.search(r'<article[^>]*id="dic_area"[^>]*>(.*?)</article>', doc, re.S) \
        or re.search(r'id="newsct_article"[^>]*>(.*?)</div>\s*</div>', doc, re.S)
    if m:
        body = re.sub(r"<br\s*/?>", "\n", m.group(1))
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S)
        body = re.sub(r'<em[^>]*class="img_desc"[^>]*>.*?</em>', "", body, flags=re.S)   # 사진 설명 제거
        text = _strip_tags(body)
    else:
        og = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', doc)
        text = html.unescape(og.group(1)) if og else ""
    return re.sub(r"\s+", " ", text).strip()[:BODY_CHARS]


def attach_bodies(items: list) -> None:
    for it in items:
        it["body"] = get_body(it["link"])
        time.sleep(0.5)


# ---------------------------------------------------------------------
# Gemini (무료 등급)
# ---------------------------------------------------------------------
def gemini_json(prompt: str, key: str, preferred: str, timeout: int = 120):
    """프롬프트를 보내고 JSON 결과를 돌려줘요. 실패하면 None."""
    if not key:
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    models = ([preferred] if preferred else []) + [m for m in GEMINI_MODELS if m != preferred]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2):
            try:
                r = requests.post(url, params={"key": key}, json=body, timeout=timeout)
                if r.status_code == 429 and attempt == 0:      # 무료 한도 초과 → 잠깐 쉬고 한 번 더
                    log(f"Gemini 한도 초과({model}), 30초 대기")
                    time.sleep(30)
                    continue
                if r.status_code != 200:                       # 404(종료)·503(혼잡) 등 → 다음 모델
                    log(f"Gemini {r.status_code}({model}): {r.text[:120]}")
                    break
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
                data = json.loads(text)
                log(f"Gemini 완료 ({model})")
                return data
            except Exception as e:
                log(f"Gemini 실패({model}): {e}")
                break
    return None


SUMMARY_PROMPT = """너는 한국 주식 단기 트레이더(종가베팅 위주)를 돕는 뉴스 요약가야.
아래 기사들을 각각 자세히 요약해서 JSON 배열로만 답해. 다른 말은 쓰지 마.

각 원소 형식:
{"i": 기사번호(정수),
 "headline": "핵심 한 줄 (무엇이 왜 일어났는지, 40자 안팎)",
 "points": ["세부 내용 3~4개. 숫자(금액·%·날짜), 원인, 앞으로의 일정 위주. 각 60자 이내"],
 "stocks": ["기사에 나온 종목명, 최대 5개"],
 "themes": ["관련 테마·업종, 최대 3개"]}

규칙:
- 기사에 없는 내용은 지어내지 마. 본문이 없으면 제목으로만 짧게 쓰고 points 는 1개만.
- stocks·themes 는 기사에 실제로 나온 것만. 없으면 빈 배열.
- 투자 권유·전망 문장은 쓰지 마.

기사:
"""

PICK_PROMPT = """너는 한국 주식 단기 트레이더(종가베팅 위주)를 돕는 뉴스 편집자야.
밤사이 나온 증권 기사 제목 목록이야. 오늘 국내 증시(특정 종목·테마·지수)에 영향이 클 만한 순서로
최대 {n}개를 골라 기사번호 배열로만 답해. 예: [3, 0, 12]
같은 사건을 다룬 기사가 여러 개면 가장 구체적인 하나만 골라.

기사:
"""


def summarize(items: list, cfg: dict) -> dict:
    if not items:
        return {}
    parts = [f"[{n}] 제목: {it['title']}\n언론사: {it['source']}\n본문: {it.get('body') or '(본문 없음)'}\n"
             for n, it in enumerate(items)]
    data = gemini_json(SUMMARY_PROMPT + "\n".join(parts), cfg["gemini_key"], cfg["gemini_model"])
    out = {}
    for row in data or []:
        try:
            out[int(row["i"])] = row
        except Exception:
            pass
    return out


def pick_important(items: list, n: int, cfg: dict) -> list:
    """제목만 보고 중요한 기사 n개를 골라요. 실패하면 최신순 n개."""
    if len(items) <= n:
        return list(range(len(items)))
    lines = [f"[{i}] {it['title']} ({it['source']})" for i, it in enumerate(items)]
    data = gemini_json(PICK_PROMPT.format(n=n) + "\n".join(lines), cfg["gemini_key"], cfg["gemini_model"], 60)
    picked = []
    for v in data if isinstance(data, list) else []:
        try:
            v = int(v)
        except Exception:
            continue
        if 0 <= v < len(items) and v not in picked:
            picked.append(v)
    if not picked:
        picked = list(range(n))          # items 는 최신순
    return picked[:n]


# ---------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------
def send_telegram(token: str, chat: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                     "disable_web_page_preview": "true"}, timeout=20)
        if r.status_code != 200:
            log(f"텔레그램 실패 {r.status_code}: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        log(f"텔레그램 실패: {e}")
        return False


def send_all(cfg: dict, msgs: list) -> bool:
    ok = True
    for m in msgs:
        ok = send_telegram(cfg["tg_token"], cfg["tg_chat"], m) and ok
        time.sleep(1)
    return ok


def article_block(it: dict, s) -> str:
    e = html.escape
    meta = e(it["source"]) + (" · " + e(it["time_text"]) if it.get("time_text") else "")
    lines = [f"<b>{e(it['title'])}</b>", f"<i>{meta}</i>"]
    if s:
        if s.get("headline"):
            lines.append(f"<b>핵심:</b> {e(s['headline'])}")
        for p in (s.get("points") or [])[:4]:
            if p:
                lines.append(f"• {e(p)}")
        rel = [x for x in (s.get("stocks") or []) if x][:5]
        th = [x for x in (s.get("themes") or []) if x][:3]
        if rel or th:
            lines.append("<b>관련:</b> " + e(", ".join(rel)) + (" · " if rel and th else "") + e(", ".join(th)))
    lines.append(f'<a href="{e(it["link"], quote=True)}">기사 보기</a>')
    return "\n".join(lines)


def chunk(head: str, blocks: list, sep: str = "\n\n") -> list:
    """텔레그램 한 메시지 최대 4096자 → 나눠서."""
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + len(sep) > 3900:
            msgs.append(cur)
            cur = b
        else:
            cur += sep + b
    msgs.append(cur)
    return msgs


# ---------------------------------------------------------------------
# 할 일 3가지
# ---------------------------------------------------------------------
def fetch_new(seen: list):
    items = get_section_news()
    if not items:
        log("기사 목록을 못 가져왔어요.")
        return None
    seen_set = set(seen)
    return [it for it in items if article_key(it["link"]) not in seen_set]   # 최신순


def run_collect(seen: list) -> None:
    """밤·주말: 보내지 않고 모아만 둬요."""
    new = fetch_new(seen)
    if not new:
        return
    queue = read_json(QUEUE_FILE, [])
    now = datetime.now(KST).isoformat(timespec="minutes")
    for it in reversed(new):                      # 오래된 것부터 뒤에 붙이기
        it["collected"] = now
        queue.append(it)
    write_json(QUEUE_FILE, queue[-QUEUE_MAX:])
    write_json(SEEN_FILE, (seen + [article_key(it["link"]) for it in reversed(new)])[-2000:])
    log(f"모아 둠 {len(new)}건 (쌓인 기사 {min(len(queue), QUEUE_MAX)}건)")


def run_live(seen: list, cfg: dict, first_run: bool) -> None:
    """낮: 새 기사를 바로 자세히 요약해서 보내요."""
    new = fetch_new(seen)
    if not new:
        log("새 기사 없음")
        return
    to_send = new[:FIRST_RUN_SEND] if first_run else new[:MAX_PER_RUN]
    attach_bodies(to_send)
    sums = summarize(to_send, cfg)
    head = f"📰 <b>{datetime.now(KST):%H:%M} 새 증권 뉴스 {len(to_send)}건</b>"
    if not sums:
        head += "\n<i>(요약을 못 해서 제목만 보내요)</i>"
    if len(new) > len(to_send):
        head += f"\n<i>새 기사 {len(new)}건 중 최신 {len(to_send)}건</i>"
    blocks = [article_block(it, sums.get(n)) for n, it in enumerate(to_send)]
    if send_all(cfg, chunk(head, blocks)):
        write_json(SEEN_FILE, (seen + [article_key(it["link"]) for it in reversed(new)])[-2000:])
        log(f"전송 {len(to_send)}건 (새 기사 {len(new)}건)")


def run_digest(seen: list, cfg: dict) -> None:
    """06시: 밤사이 모아 둔 기사(+ 지금 새로 보이는 기사)를 한 번에 보내요."""
    queue = read_json(QUEUE_FILE, [])
    new = fetch_new(seen) or []
    now_s = datetime.now(KST).isoformat(timespec="minutes")
    for it in reversed(new):
        it["collected"] = now_s
        queue.append(it)
    if not queue:
        log("밤사이 기사 없음")
        write_json(SEEN_FILE, (seen + [article_key(it["link"]) for it in reversed(new)])[-2000:])
        return
    items = list(reversed(queue))                 # 최신순
    for it in items:                              # '1분전' 같은 표시는 모을 때 기준이라, 모은 시각으로 바꿔요
        try:
            c = datetime.fromisoformat(it.get("collected") or now_s)
            it["time_text"] = f"{c:%m/%d %H:%M}경"
        except Exception:
            pass
    since = min((it.get("collected") or now_s) for it in queue)
    try:
        since_dt = datetime.fromisoformat(since)
        since_txt = f"{since_dt:%m/%d %H}시"
    except Exception:
        since_txt = "어젯밤"

    picked = pick_important(items, DIGEST_DETAIL, cfg)
    detail = [items[i] for i in picked]
    rest = [it for i, it in enumerate(items) if i not in set(picked)]
    attach_bodies(detail)
    sums = summarize(detail, cfg)

    head = (f"🌙 <b>밤사이 증권 뉴스</b> ({since_txt} 이후 {len(items)}건)\n"
            f"<i>중요한 {len(detail)}건 자세히{' · 나머지는 아래 제목 목록' if rest else ''}</i>")
    if not sums:
        head += "\n<i>(요약을 못 해서 제목만 보내요)</i>"
    msgs = chunk(head, [article_block(it, sums.get(n)) for n, it in enumerate(detail)])
    if rest:
        e = html.escape
        lines = [f'• <a href="{e(it["link"], quote=True)}">{e(it["title"])}</a> <i>{e(it["source"])}</i>' for it in rest]
        msgs += chunk(f"📋 <b>그 외 밤사이 기사 {len(rest)}건</b>", lines, sep="\n")
    if send_all(cfg, msgs):
        write_json(QUEUE_FILE, [])
        write_json(SEEN_FILE, (seen + [article_key(it["link"]) for it in reversed(new)])[-2000:])
        log(f"밤사이 묶음 전송 {len(items)}건 (자세히 {len(detail)}건)")


# ---------------------------------------------------------------------
def main() -> None:
    now = datetime.now(KST)
    if "--digest" in sys.argv:
        mode = "digest"
    elif "--now" in sys.argv:
        mode = "live"
    elif now.weekday() in WEEKDAYS and now.hour == DIGEST_HOUR:
        mode = "digest"
    elif now.weekday() in WEEKDAYS and now.hour in LIVE_HOURS:
        mode = "live"
    else:
        mode = "collect"

    seen_raw = read_json(SEEN_FILE, None)
    first_run = seen_raw is None
    seen = seen_raw or []

    if mode == "collect":
        if first_run:                 # 기록이 없으면 지금 보이는 기사는 '본 것'으로만 표시
            items = get_section_news()
            write_json(SEEN_FILE, [article_key(it["link"]) for it in reversed(items)])
            return
        run_collect(seen)
        return

    cfg = load_secrets()
    if mode == "digest":
        run_digest(seen, cfg)
    else:
        run_live(seen, cfg, first_run)


if __name__ == "__main__":
    main()

"""
네이버 증권 뉴스 → 새 기사만 골라 → 본문 AI 요약(Gemini 무료) → 텔레그램 전송

- 오라클 서버에서 cron으로 매시 정각에 실행돼요.
- 평일 08~20시(한국 시간)에만 동작하고, 그 외 시간에는 아무것도 안 하고 끝나요.
- 이미 보낸 기사는 ~/dashboard_data/news_seen.json 에 기록해서 다시 안 보내요.
- 비밀 값(텔레그램 토큰, Gemini 키)은 ~/.streamlit/secrets.toml 에서 읽어요. 이 파일에는 없어요.

실행:
  python news_bot.py          # 시간 확인 후 실행 (cron용)
  python news_bot.py --now    # 시간 상관없이 바로 실행 (테스트용)
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

SECTION = ("101", "258")          # 네이버 뉴스 경제 > 증권
LIST_LIMIT = 30                   # 목록에서 한 번에 보는 기사 수
MAX_PER_RUN = 15                  # 한 번에 요약해서 보내는 최대 기사 수
FIRST_RUN_SEND = 5                # 처음 실행할 때는 최신 5건만 보내고 나머지는 '본 것'으로 처리
BODY_CHARS = 1500                 # 요약에 넘기는 본문 길이(글자)
ACTIVE_HOURS = range(8, 21)       # 08:00 ~ 20:59 (한국 시간)
ACTIVE_WEEKDAYS = range(0, 5)     # 월~금

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
GEMINI_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]


# ---------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------
def load_secrets() -> dict:
    with open(SECRETS, "rb") as f:
        s = tomllib.load(f)
    tg = s.get("telegram", {})
    gm = s.get("gemini", {})
    if not tg.get("token") or not tg.get("chat_id"):
        sys.exit("secrets.toml 에 [telegram] token / chat_id 가 없어요.")
    return {
        "tg_token": tg["token"],
        "tg_chat": str(tg["chat_id"]),
        "gemini_key": gm.get("api_key", ""),
        "gemini_model": gm.get("model", ""),
    }


def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


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
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
        except Exception as e:
            log(f"목록 실패 {url}: {e}")
            continue
        items = parse_naver_news(r.text, limit)
        if items:
            return items
    return []


# ---------------------------------------------------------------------
# 기사 본문
# ---------------------------------------------------------------------
def article_key(link: str) -> str:
    """같은 기사를 한 번만 보내도록 링크에서 (언론사번호/기사번호)를 뽑아요."""
    m = re.search(r"/article/(\d+)/(\d+)", link) or re.search(r"oid=(\d+).*?aid=(\d+)", link)
    return f"{m.group(1)}/{m.group(2)}" if m else link


def get_body(link: str) -> str:
    try:
        r = requests.get(link, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log(f"본문 실패 {link}: {e}")
        return ""
    doc = r.text
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
    text = re.sub(r"\s+", " ", text).strip()
    return text[:BODY_CHARS]


# ---------------------------------------------------------------------
# AI 요약 (Gemini 무료 등급, 새 기사를 묶어서 한 번에)
# ---------------------------------------------------------------------
PROMPT = """너는 한국 주식 단기 트레이더(종가베팅 위주)를 돕는 뉴스 요약가야.
아래 기사들을 각각 요약해서 JSON 배열로만 답해. 다른 말은 쓰지 마.

각 원소 형식:
{"i": 기사번호(정수), "summary": "2~3문장 요약. 숫자·종목명·이유 중심", "tags": ["관련 종목 또는 테마", ...]}

규칙:
- 기사에 없는 내용은 지어내지 마.
- tags 는 기사에 실제로 나온 종목명·테마만, 최대 4개. 없으면 빈 배열.
- 투자 권유 문장은 쓰지 마.

기사:
"""


def summarize(items: list, key: str, preferred: str) -> dict:
    if not key or not items:
        return {}
    parts = []
    for n, it in enumerate(items):
        parts.append(f"[{n}] 제목: {it['title']}\n언론사: {it['source']}\n본문: {it.get('body') or '(본문 없음)'}\n")
    body = {
        "contents": [{"parts": [{"text": PROMPT + "\n".join(parts)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    models = ([preferred] if preferred else []) + [m for m in GEMINI_MODELS if m != preferred]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2):
            try:
                r = requests.post(url, params={"key": key}, json=body, timeout=60)
                if r.status_code == 429:          # 무료 한도 초과 → 잠깐 쉬고 한 번 더
                    log(f"Gemini 한도 초과({model}), 30초 대기")
                    time.sleep(30)
                    continue
                if r.status_code == 404:          # 모델 이름이 없음 → 다음 모델
                    log(f"Gemini 모델 없음: {model}")
                    break
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
                out = {}
                for row in json.loads(text):
                    try:
                        out[int(row["i"])] = row
                    except Exception:
                        pass
                log(f"요약 완료 {len(out)}건 ({model})")
                return out
            except Exception as e:
                log(f"Gemini 실패({model}): {e}")
                break
    return {}


# ---------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------
def send_telegram(token: str, chat: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                     "disable_web_page_preview": "true"}, timeout=15)
        if r.status_code != 200:
            log(f"텔레그램 실패 {r.status_code}: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        log(f"텔레그램 실패: {e}")
        return False


def build_messages(items: list, sums: dict) -> list:
    now = datetime.now(KST)
    head = f"📰 <b>{now:%H:%M} 새 증권 뉴스 {len(items)}건</b>"
    if not sums:
        head += "\n<i>(요약을 못 해서 제목만 보내요)</i>"
    blocks = []
    for n, it in enumerate(items):
        e = html.escape
        lines = [f"<b>{e(it['title'])}</b>",
                 f"<i>{e(it['source'])}{' · ' + e(it['time_text']) if it.get('time_text') else ''}</i>"]
        s = sums.get(n)
        if s and s.get("summary"):
            lines.append(e(s["summary"]))
            tags = [t for t in (s.get("tags") or []) if t][:4]
            if tags:
                lines.append("🏷 " + e(" · ".join(tags)))
        lines.append(f'<a href="{e(it["link"], quote=True)}">기사 보기</a>')
        blocks.append("\n".join(lines))
    # 텔레그램 한 메시지 최대 4096자 → 나눠서 보내기
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 2 > 3900:
            msgs.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    msgs.append(cur)
    return msgs


# ---------------------------------------------------------------------
# 본 기사 기록
# ---------------------------------------------------------------------
def load_seen():
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_seen(keys: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(keys[-1000:], ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------
def main() -> None:
    force = "--now" in sys.argv
    now = datetime.now(KST)
    if not force and (now.weekday() not in ACTIVE_WEEKDAYS or now.hour not in ACTIVE_HOURS):
        return
    cfg = load_secrets()

    items = get_section_news()
    if not items:
        log("기사 목록을 못 가져왔어요.")
        return

    seen = load_seen()
    first_run = seen is None
    seen = seen or []
    seen_set = set(seen)
    new = [it for it in items if article_key(it["link"]) not in seen_set]   # 목록은 최신순
    if not new:
        log("새 기사 없음")
        return

    to_send = new[:FIRST_RUN_SEND] if first_run else new[:MAX_PER_RUN]
    for it in to_send:
        it["body"] = get_body(it["link"])
        time.sleep(0.5)

    sums = summarize(to_send, cfg["gemini_key"], cfg["gemini_model"])
    ok = True
    for msg in build_messages(to_send, sums):
        ok = send_telegram(cfg["tg_token"], cfg["tg_chat"], msg) and ok
        time.sleep(1)

    if ok:   # 보내기에 성공했을 때만 '본 것'으로 기록 (실패하면 다음 시간에 다시 시도)
        save_seen(seen + [article_key(it["link"]) for it in reversed(new)])
        log(f"전송 {len(to_send)}건 (새 기사 {len(new)}건)")


if __name__ == "__main__":
    main()

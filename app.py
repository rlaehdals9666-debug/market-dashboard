"""
오늘의 시장 v3 - 개인 마켓 대시보드
탭: 지수 · 거래대금 · 조회순위 · 관심 · 일정

- 국내 지수/종목 시세: 네이버 증권 공개 데이터 (실시간)
- 투자자 매매동향: 네이버 증권 공개 데이터
- 거래대금/조회순위/프로그램 매매: 키움 REST API (조회 전용, 주문 기능 없음)
- 미국 지수·선물, VIX, 유가: 야후 파이낸스 (약 10~15분 지연)
- 관심종목과 일정은 휴대폰에서 편집 가능 (PIN 필요), 서버의 ~/dashboard_data 에 저장

실행: py -m streamlit run app.py
"""

import html
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

# =====================================================================
# 설정
# =====================================================================
DEFAULT_WATCHLIST = {
    "kr": [
        {"name": "삼성전자", "code": "005930", "market": "KS"},
        {"name": "SK하이닉스", "code": "000660", "market": "KS"},
        {"name": "NAVER", "code": "035420", "market": "KS"},
    ],
    "us": [
        {"name": "엔비디아", "ticker": "NVDA"},
        {"name": "애플", "ticker": "AAPL"},
        {"name": "테슬라", "ticker": "TSLA"},
    ],
}
TRADE_VALUE_MIN_EOK = 1000     # 거래대금 탭: 이 금액(억원) 이상만 표시
TRADE_VALUE_TO_EOK = 0.01      # 키움 거래대금(백만원) → 억원
INDEX_REFRESH = 10             # 지수 탭 갱신(초)
RANK_REFRESH = 15              # 거래대금·조회순위 탭 갱신(초)
WATCH_REFRESH = 5              # 관심 탭 갱신(초)
PROGRAM_MAX_STOCKS = 10        # 프로그램 매매를 조회할 최대 종목 수
LIMIT_RATE = 29.5               # 상한가/하한가로 볼 등락률 기준(%)
KR_FUND_KEYWORDS = (
    "ETN", "ETF", "KODEX", "TIGER", "KBSTAR", "ACE ", "SOL ", "HANARO", "PLUS ",
    "RISE ", "KOSEF", "TIMEFOLIO", "히어로즈", "마이다스", "파워", "웰스", "네비게이터",
    "FOCUS", "코세프", "레버리지", "인버스", "합성",
)
# =====================================================================

KST = ZoneInfo("Asia/Seoul")
APP_DIR = Path(__file__).parent
DATA_DIR = Path.home() / "dashboard_data"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
USER_SCHEDULE_FILE = DATA_DIR / "schedule.json"
NOTES_FILE = DATA_DIR / "notes.json"
REPO_SCHEDULE_FILE = APP_DIR / "schedule.json"
KIWOOM_HOST = "https://api.kiwoom.com"

COUNTRIES = {"US": "미국", "KR": "한국", "CN": "중국", "JP": "일본", "EU": "유로존", "UK": "영국", "DE": "독일"}
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
UP, DOWN, FLAT = "#F04452", "#3182F6", "#8B95A1"
INVESTOR_COLORS = {"개인": "#FFB400", "외국인": "#3182F6", "기관": "#00B868"}
AVATAR_COLORS = ["#3182F6", "#F04452", "#00B868", "#FF8A00", "#7B61FF", "#00A9C5", "#4E5968"]
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Referer": "https://m.stock.naver.com/",
}

st.set_page_config(page_title="오늘의 시장", page_icon="📈", layout="centered")

# ---------------------------------------------------------------------
# 스타일
# ---------------------------------------------------------------------
CSS = """<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');
:root{
  --bg:#F2F4F6; --card:#FFFFFF; --text:#191F28; --text2:#4E5968; --sub:#8B95A1;
  --line:#F2F4F6; --blue:#3182F6; --up:#F04452; --down:#3182F6;
  --font:'Pretendard Variable',Pretendard,-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
}
.stApp{background:var(--bg);}
.stApp *:not([data-testid="stIconMaterial"]){font-family:var(--font);}
header[data-testid="stHeader"]{display:none;}
.block-container{max-width:640px;padding:1.25rem 1rem 4rem;}

.stTabs [data-baseweb="tab-list"]{gap:2px;background:#E5E8EB;padding:4px;border-radius:14px;}
.stTabs [data-baseweb="tab-list"]{overflow-x:auto;}
.stTabs [data-baseweb="tab"]{flex:1;justify-content:center;height:36px;padding:0 1px;border-radius:9px;background:transparent;min-width:0;}
.stTabs [data-baseweb="tab"] p{font-size:12px;font-weight:600;color:var(--sub);white-space:nowrap;}
.stTabs [aria-selected="true"]{background:#FFFFFF;box-shadow:0 1px 3px rgba(0,0,0,.08);}
.stTabs [aria-selected="true"] p{color:var(--text);}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{display:none;}
.stTabs [data-baseweb="tab-panel"]{padding-top:12px;}

[data-baseweb="select"]>div{background:var(--bg);border:none;border-radius:12px;}
[data-baseweb="input"],[data-baseweb="base-input"]{background:#FFFFFF !important;border:none !important;border-radius:12px;}
[data-testid="stVerticalBlockBorderWrapper"]{background:#FFFFFF;border:none !important;border-radius:20px;}
[data-testid="stExpander"] details{background:#FFFFFF;border:none;border-radius:16px;}
button[data-testid="stBaseButton-pills"],button[data-testid="stBaseButton-segmented_control"]{background:#FFFFFF;border-color:#FFFFFF;}
button[data-testid="stBaseButton-pillsActive"],button[data-testid="stBaseButton-segmented_controlActive"]{background:var(--text) !important;border-color:var(--text) !important;}
button[data-testid="stBaseButton-pillsActive"] p,button[data-testid="stBaseButton-segmented_controlActive"] p{color:#FFFFFF !important;}

.tx{color:var(--text);letter-spacing:-0.01em;}
.hero{padding:4px 4px 14px;}
.hero-date{font-size:14px;font-weight:500;color:var(--sub);}
.hero-title{font-size:26px;font-weight:700;margin-top:2px;}
.updated{font-size:12px;color:var(--sub);padding:0 4px;}
.section-title{font-size:19px;font-weight:700;padding:16px 4px 8px;}
.card{background:var(--card);border-radius:20px;padding:18px 20px;}
.card-title{font-size:17px;font-weight:700;margin-bottom:4px;}
.empty{font-size:14px;color:var(--sub);padding:6px 0;}
.num{font-variant-numeric:tabular-nums;}
.up{color:var(--up);} .down{color:var(--down);} .flat{color:var(--sub);}
.chg{font-size:13px;font-weight:500;font-variant-numeric:tabular-nums;}
.stack{display:flex;flex-direction:column;gap:8px;}

/* 지수 카드 */
.ix-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;}
.ix-name{font-size:15px;font-weight:600;color:var(--text2);}
.ix-time{font-size:12px;color:var(--sub);}
.ix-val{font-size:28px;font-weight:700;margin:4px 0 2px;font-variant-numeric:tabular-nums;}
.inv-label{font-size:12px;color:var(--sub);margin:14px 0 6px;}
.inv{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;}
.inv div{background:var(--bg);border-radius:12px;padding:8px 10px;}
.inv .k{font-size:12px;color:var(--sub);}
.inv .v{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px;}
.mini-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;}
.mini{background:var(--card);border-radius:16px;padding:14px 16px;}
.mini .n{font-size:13px;font-weight:500;color:var(--sub);}
.mini .p{font-size:18px;font-weight:700;margin:4px 0 2px;font-variant-numeric:tabular-nums;}
.detail{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:12px;}
.detail div{background:var(--bg);border-radius:12px;padding:8px 10px;}
.detail .k{font-size:12px;color:var(--sub);}
.detail .v{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px;}

/* 리스트 */
.row{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line);color:inherit !important;text-decoration:none !important;}
.card-title + .row,.list > .row:first-child{border-top:none;}
.rank-no{width:24px;flex-shrink:0;text-align:center;font-size:15px;font-weight:700;color:var(--blue);font-variant-numeric:tabular-nums;}
.avatar{position:relative;width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700;flex-shrink:0;overflow:hidden;}
.avatar img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;border-radius:50%;}
.row-main{flex:1;min-width:0;}
.row-name{font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.row-sub{font-size:13px;color:var(--sub);margin-top:2px;}
.row-right{text-align:right;}
.row-price{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;}
.rank-move{font-size:12px;font-weight:600;margin-left:6px;}
.tap-hint{font-size:12px;color:var(--sub);padding:0 4px 6px;}

/* 관심종목 + 프로그램 매매 */
.prog{padding:14px 0;border-top:1px solid var(--line);}
.card-title + .prog{border-top:none;}
.prog-top{display:flex;align-items:center;gap:12px;color:inherit !important;text-decoration:none !important;}
.prog-net{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums;}
.prog-bar{display:flex;height:8px;border-radius:999px;overflow:hidden;background:#E5E8EB;margin:12px 0 8px;}
.prog-bar .b{background:var(--up);} .prog-bar .s{background:var(--down);}
.prog-nums{display:flex;justify-content:space-between;font-size:13px;color:var(--sub);font-variant-numeric:tabular-nums;}
.prog-nums b{font-weight:600;color:var(--text2);margin-left:4px;}

/* 일정 */
.day-head{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:700;padding:10px 4px 8px;}
.today{background:var(--blue);color:#FFFFFF;font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;}
.ev-card{padding:6px 20px;}
.ev{display:flex;align-items:flex-start;gap:14px;padding:12px 0;border-top:1px solid var(--line);}
.ev:first-child{border-top:none;}
.ev-time{width:44px;flex-shrink:0;font-size:14px;font-weight:600;padding-top:1px;font-variant-numeric:tabular-nums;}
.ev-main{flex:1;min-width:0;}
.ev-title{font-size:15px;font-weight:600;line-height:1.4;}
.ctry{display:inline-block;font-size:11px;font-weight:600;color:var(--text2);background:var(--bg);border-radius:6px;padding:2px 6px;margin-right:6px;vertical-align:1px;}
.ev-nums{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--sub);margin-top:4px;}
.ev-nums b{font-weight:500;color:var(--text2);margin-right:3px;}
.ev-imp{display:flex;gap:3px;padding-top:7px;}
.dot{display:block;width:6px;height:6px;border-radius:50%;background:#E5E8EB;}
.dot.on{background:var(--blue);}

/* 부드러운 화면: 갱신 중 흐려짐·깜빡임 없애기 */
[data-stale="true"],[data-stale="true"] *{opacity:1 !important;transition:none !important;}
[data-testid="stStatusWidget"],[data-testid="stToolbar"],[data-testid="stDecoration"]{display:none !important;}
html{scroll-behavior:smooth;-webkit-tap-highlight-color:transparent;}
/* 차트 확대(전체화면) 아이콘을 크게 */
[data-testid="stElementToolbar"]{transform:scale(1.4);transform-origin:top right;}
[data-testid="stElementToolbarButton"]{padding:6px !important;}
[data-testid="StyledFullScreenButton"]{transform:scale(1.4);transform-origin:top right;}
.card,.mini{box-shadow:0 1px 3px rgba(0,27,55,.04);}
.card{border-radius:22px;}
.mini{border-radius:18px;}
.ix-val,.row-price,.prog-net,.chg,.inv .v,.mini .p{transition:color .4s ease;}
a.row,.prog-top{border-radius:12px;transition:background .15s ease;}
a.row:active,.prog-top:active{background:#F9FAFB;opacity:1;}
.stTabs [data-baseweb="tab"]{transition:background .2s ease,box-shadow .2s ease;}
.prog-bar div{transition:width .6s ease;}
[data-testid="stVerticalBlock"]{gap:.75rem;}
html,body{touch-action:manipulation;}
@keyframes fadein{from{opacity:0;transform:translateY(4px);}to{opacity:1;transform:none;}}
.stTabs [data-baseweb="tab-panel"]{animation:fadein .22s ease;}
button,a{-webkit-user-select:none;user-select:none;}
button:active{transform:scale(.97);transition:transform .08s ease;}

/* 지수 카드 (v4) */
.ix-row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
.ix-row .ix-val{margin:2px 0;}
.inv-line{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--sub);background:var(--bg);
  border-radius:10px;padding:7px 10px;white-space:nowrap;overflow-x:auto;margin-top:6px;}
.inv-line .lbl{font-weight:700;color:var(--text2);}
.inv-line b{font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums;margin-left:2px;}
.inv-note{font-size:11.5px;color:var(--sub);padding:6px 2px 0;}

/* 오일/간단 리스트 한 줄 */
.row-simple{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-top:1px solid var(--line);}
.row-simple:first-child{border-top:none;}
.row-simple .n{font-size:15px;font-weight:600;}
.row-simple .r{display:flex;align-items:baseline;gap:8px;}
.row-simple .p{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;}

/* 관심종목 편집: 한 줄 삭제 */
.wl-item{display:flex;align-items:center;gap:10px;padding:6px 0;}
.wl-item .n{flex:1;font-size:14px;}
div[data-testid="stHorizontalBlock"] button[kind="secondary"]{min-height:0;}

/* MA 범례 */
.ma-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--sub);padding:4px 2px 0;}
.ma-legend span{display:inline-flex;align-items:center;gap:4px;}
.ma-legend i{display:inline-block;width:10px;height:2px;border-radius:2px;}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

# 홈 화면 아이콘: iOS의 기본 회색 글자 아이콘 대신 앱 아이콘을 붙여요.
APP_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAAF9UlEQVR42u2dy3MURRyAe6b2pIIGNYIKOQglxWYhFIiC/iXgo0rDUQ9yCCE8EiDCBQ96pQoQ8S/xUZYHCJEqLPGCAQmRIL6u4yFAduP07Dy6e37T/X01RQE7M53t/vb3mJnKRsoJ24//q8Acl48+4WCUyN6pR6b+YRUdcOXYk42RAye8sSTCCSyxKMfI1N+sh0hFnqpTjpFJtBCvyGR5RWLM8JsqyxShBSHEmBzbJv9iohvKzOQqi3JsO4YZDfdjapV5OdAiQEWifGb8yYR658dqA90KZnhJnmWNMQM/dLT6nCBhDsMlq+bYevQBE+Q9V48/XTitYEYgZCx0jBmgW25NzZFQa0Ba5Nh65A/mJbjgkbboMWaAbuljJgVytbKdI/eZkcCZPTGgKUgpQyE1rXQOEzagRwNqDndcPfFM3/8RWnN0Di+yfjbNGOjqC+6v+KessuPkmp7I0ZlYVIlis7hpRHlY6knaOhOLpBWHYePkQOlXa6T1yF0aFetROtsPgWk9VkoNT9xjCaGbJSVIK/WHjaJ7ur3OQbXopA5dbgwnNBlE2E8eK+ywts1O64JBujWz02tE2RENH/qd4G8loUw/qwkb93LuICOtAKReIW2PLzALxvnxk+fSu4DeOJ1zNyJHcGZkSKA7A3KAqFYW6ggb8oNHTMNpcNObsZBx1PChBb0fdb6dFjdVXJA4P9BMt3LwLmtnJqGcej49bIwvZLzad4fh+trJFvdjrdIev3vt1GAeq3R71rhA0ZaD8yxhdfoaUNGwmgpS6sjKm1UzHppXx/uilQVaWWvbtdODLtLW6UH3by3aMnaHj4iJxXvBYs1RU11IWpG+fu36OgYKUmNbe2y+PTavlFr6s5IQj87THpuv8R1xncMwRtJ0ohIJ6Z60AsgBxWnx678kImNRiByAHIAcYLLmoJWVWHLIWBSeBBNqB2kFhLeyhA5CB5EDqDkIHEQOoJWllSVygESQAyhI9Vw/87Lupc0H5kIuSIO+znH9zPo83mw+8GuYdsSYYXBPao6wzAjZj1YS3pNgP326oZxPr35801FS4UkwIK34EDaqH4sc4FfNwS17kU0mT4Lhhmg3SCuQ1coSOop9pBNvRiFyADUHNQeRA2hlCR3UHCAb5ICMVpbfz1Eo3DuZLu7KAmkFkAN8bGWpOQqWA/6MQuSACpFDMD9/9orupU0f/cLiBSpHhhbdO6CIXTkEXue48fnG/A5t/PCG25KD6xxNMKPc/tBUOcqtNH7Yqzn8aGUTvwYirawMAJtqORb0kcOba2AJo/hekIKkVtaX0OHTc+E8fQ7UHNQcja05fHrAGDtIK4AcgBwguZX15Ukwn+6XclcWSCvQ3LRCK0srq5cDN3CDtALIAQZbWe7KhjoKkQMoSClIiRzgNnIQOggdRA6g5mAUWllaWdIKIAcgBzStIPWlIvXpt3XxJBiQVqCxaYVWllY2o+bwxXMugpFWADlAhByJkG1o/0zptzG0fybAUWxvRA7IiByJnNihhkbLfOCGRmeCHcXqJi5yFJ3T8mvgyyj2iNZ/cFlgQLt5diTPbhtGrzBKcHL0nVaDU+nTKKbleF+uHFAvfOkwcBEMkAOQA5zUHHwjNRA5oLgcc+d2MAvwf+bO7Vj6dkgyC6TVHLgB1BxQSo658zuZCOgpOM7vVI+/kTohtQBpBfITPf7bS+/9wHSAUurWhde6uhWlaGiBtAKl5Lh1YRfTAd0atHpeIbFAakG6xIvvfs+kBMvtL16n5oBSBekKdyDYsJEeOfADM9IK0uXKlNIUNDXH7YtvMDUBhQ3NcsdFD4BAzEhpZVew7p3vmD6P+e3i7oxXW32Opvag5tCa9eVu5sjbsNFvcePqpwAvzehfcywXH29/y4R6ZMaePLtFhU6KIoFoUUYOpdS6ffjRWDMu7Sm0f1RijLX7vmGiG8edS28WPSQqPRiKeKxF3m7F+JDQCDMqRY7lELKXECJSi6+qfnojUz/K2r1fsx5itHjLyHki4z8ZljTdCYtyYEnTnXAhB6I0UYhu/gOBMEnI8vvZkAAAAABJRU5ErkJggg=="
_ICON_JS = """
<script>
(function() {
  try {
    var doc = window.parent.document;
    var href = "data:image/png;base64,__B64__";
    var tags = [
      ["link", {rel: "apple-touch-icon", href: href}],
      ["link", {rel: "icon", href: href}],
      ["meta", {name: "apple-mobile-web-app-capable", content: "yes"}],
      ["meta", {name: "apple-mobile-web-app-status-bar-style", content: "black-translucent"}],
      ["meta", {name: "apple-mobile-web-app-title", content: "__TITLE__"}],
      ["meta", {name: "theme-color", content: "#3182F6"}]
    ];
    tags.forEach(function(t) {
      var tag = doc.createElement(t[0]);
      Object.keys(t[1]).forEach(function(k) { tag.setAttribute(k, t[1][k]); });
      doc.head.appendChild(tag);
    });
  } catch (e) {}
})();
</script>
""".replace("__B64__", APP_ICON_B64).replace("__TITLE__", "\uc624\ub298\uc758 \uc2dc\uc7a5")
components.html(_ICON_JS, height=0)


def show(html_str: str) -> None:
    st.markdown(html_str, unsafe_allow_html=True)


# ---------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------
def to_num(v) -> float:
    try:
        s = str(v).replace(",", "").replace("+", "").replace("%", "").strip()
        for unit in ("억", "원", "주", "백만"):
            s = s.replace(unit, "")
        return float(s or 0)
    except ValueError:
        return 0.0


def pick(d: dict, *keys, default=""):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def first_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def get_json(url: str, params: dict | None = None, timeout: int = 6):
    r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def sign_cls(v: float) -> str:
    return "up" if v > 0 else "down" if v < 0 else "flat"


def signed(v: float, fmt: str = ",.2f") -> str:
    return f"{'+' if v > 0 else ''}{v:{fmt}}"


def won(v: float) -> str:
    return f"{v:,.0f}원"


def usd(v: float) -> str:
    return f"&#36;{v:,.2f}"


def eok(v: float) -> str:
    return f"{'+' if v > 0 else ''}{v:,.0f}억"


def kst_str(ts) -> str:
    try:
        return pd.Timestamp(ts).tz_convert(KST).strftime("%m/%d %H:%M")
    except Exception:
        try:
            return datetime.fromisoformat(str(ts)).astimezone(KST).strftime("%m/%d %H:%M")
        except Exception:
            return str(ts)[:16]


def avatar(name: str, logo: str | None = None) -> str:
    color = AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]
    initial = html.escape(name[:1] or "?")
    if logo:
        return (f'<div class="avatar" style="background:#F2F4F6;color:{color}"><span>{initial}</span>'
                f'<img src="{html.escape(logo, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer"></div>')
    return f'<div class="avatar" style="background:{color};color:#FFFFFF"><span>{initial}</span></div>'


def logo_kr(code: str, market: str = "KS") -> str:
    return f"https://financialmodelingprep.com/image-stock/{code}.{market or 'KS'}.png"


def logo_us(ticker: str) -> str:
    return f"https://financialmodelingprep.com/image-stock/{ticker.upper()}.png"


def news_url(code: str) -> str:
    return f"https://m.stock.naver.com/domestic/stock/{code}/news"


def fmt_tm(tm: str) -> str:
    tm = str(tm).strip()
    if len(tm) >= 6 and tm[:6].isdigit():
        return f"{tm[:2]}:{tm[2:4]}:{tm[4:6]}"
    if len(tm) == 4 and tm.isdigit():
        return f"{tm[:2]}:{tm[2:]}"
    return tm


# ---------------------------------------------------------------------
# 저장소 (관심종목·일정) + PIN
# ---------------------------------------------------------------------
def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, data) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_watchlist() -> dict:
    wl = read_json(WATCHLIST_FILE)
    if not isinstance(wl, dict):
        return json.loads(json.dumps(DEFAULT_WATCHLIST))
    wl.setdefault("kr", [])
    wl.setdefault("us", [])
    return wl


def load_schedule():
    data = read_json(USER_SCHEDULE_FILE)
    if data is None:
        data = read_json(REPO_SCHEDULE_FILE)
    if not isinstance(data, dict):
        data = {"week": "", "events": []}
    data.setdefault("events", [])
    return data


def edit_pin():
    try:
        return str(st.secrets["app"]["edit_pin"]).strip()
    except Exception:
        return None


def pin_gate(key: str) -> bool:
    pin = edit_pin()
    if not pin:
        st.caption("편집하려면 서버의 secrets.toml에 [app] edit_pin을 설정해 주세요.")
        return False
    if st.session_state.get("unlocked"):
        return True
    value = st.text_input("PIN", type="password", key=f"pin_{key}", placeholder="편집 PIN 입력")
    if st.button("잠금 해제", key=f"unlock_{key}"):
        if value.strip() == pin:
            st.session_state["unlocked"] = True
            st.rerun()
        else:
            st.error("PIN이 맞지 않아요.")
    return False


# ---------------------------------------------------------------------
# 네이버 증권 (공개 JSON)
# ---------------------------------------------------------------------
def _apply_direction(d: dict, change: float, rate: float):
    code = str((d.get("compareToPreviousPrice") or {}).get("code", ""))
    if code in ("4", "5"):
        return -abs(change), -abs(rate)
    if code in ("1", "2"):
        return abs(change), abs(rate)
    return change, rate


@st.cache_data(ttl=5, show_spinner=False)
def naver_index(code: str) -> dict:
    data = get_json(f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}")
    d = (data.get("datas") or [{}])[0]
    change, rate = _apply_direction(d, to_num(d.get("compareToPreviousClosePrice")), to_num(d.get("fluctuationsRatio")))
    return {"price": to_num(d.get("closePrice")), "change": change, "rate": rate,
            "time": d.get("localTradedAt", ""), "status": d.get("marketStatus", "")}


@st.cache_data(ttl=3, show_spinner=False)
def naver_stocks(codes: tuple) -> dict:
    if not codes:
        return {}
    data = get_json("https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(codes))
    out = {}
    for i, d in enumerate(data.get("datas", [])):
        code = str(pick(d, "itemCode", "code", "symbolCode", default=codes[i] if i < len(codes) else ""))
        change, rate = _apply_direction(d, to_num(d.get("compareToPreviousClosePrice")), to_num(d.get("fluctuationsRatio")))
        out[code] = {"price": to_num(d.get("closePrice")), "change": change, "rate": rate,
                     "name": d.get("stockName", "")}
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def naver_search(query: str) -> list:
    data = get_json("https://m.stock.naver.com/front-api/search/autoComplete",
                    {"query": query, "target": "stock"})
    items = (data.get("result") or {}).get("items") or []
    out = []
    for it in items:
        code, type_name = str(it.get("code", "")), str(it.get("typeName", ""))
        if len(code) == 6 and ("코스피" in type_name or "코스닥" in type_name):
            out.append({"name": it.get("name", code), "code": code,
                        "market": "KQ" if "코스닥" in type_name else "KS"})
    return out[:10]


@st.cache_data(ttl=20, show_spinner=False)
def naver_index_integration(code: str):
    errors = []
    for url in (f"https://m.stock.naver.com/api/index/{code}/integration",
                f"https://stock.naver.com/api/securityFe/api/index/{code}/integration"):
        try:
            return {"data": get_json(url), "url": url, "errors": errors}
        except Exception as e:
            errors.append(f"{url} → {e}")
    return {"data": None, "url": None, "errors": errors}


def _classify_investor(key: str):
    k = key.lower()
    if any(w in k for w in ("personal", "individual", "indi", "개인")):
        return "개인"
    if any(w in k for w in ("foreign", "frgn", "외국")):
        return "외국인"
    if any(w in k for w in ("institution", "organ", "orgn", "기관")):
        return "기관"
    return None


def parse_deal_trend(integ) -> dict | None:
    """integration 응답의 투자자별 매매동향에서 개인/외국인/기관 값을 찾아요."""
    if not isinstance(integ, dict):
        return None
    info = None
    for key in ("dealTrendInfo", "dealTrendInfos", "investorTrendInfo", "dealTrend"):
        if integ.get(key):
            info = integ[key]
            break
    if isinstance(info, list):
        info = info[0] if info else None
    if not isinstance(info, dict):
        return None
    result = {}
    for k, v in info.items():
        who = _classify_investor(k)
        if who and who not in result and isinstance(v, (str, int, float)):
            result[who] = to_num(v)
    return result if len(result) >= 2 else None


@st.cache_data(ttl=30, show_spinner=False)
def naver_trend_time(market: str) -> dict:
    """시간별 투자자 매매동향 (market: KOSPI / KOSDAQ / FUT)"""
    today = datetime.now(KST).strftime("%Y%m%d")
    candidates = [
        ("https://stock.naver.com/api/domestic/market/trend/time",
         {"marketType": market, "bizdate": today, "page": 1, "pageSize": 100}),
        ("https://stock.naver.com/api/domestic/market/trend/time",
         {"marketType": market, "page": 1, "pageSize": 100}),
        (f"https://stock.naver.com/api/domestic/market/trend/time/{market}", {"page": 1, "pageSize": 100}),
        ("https://m.stock.naver.com/api/domestic/market/trend/time",
         {"marketType": market, "page": 1, "pageSize": 100}),
    ]
    errors = []
    for url, params in candidates:
        try:
            data = get_json(url, params)
            content = data.get("content") if isinstance(data, dict) else None
            if content:
                return {"rows": content, "url": url, "params": params, "errors": errors}
            errors.append(f"{url} {params} → 빈 응답")
        except Exception as e:
            errors.append(f"{url} {params} → {e}")
    return {"rows": [], "url": None, "params": None, "errors": errors}


@st.cache_data(ttl=300, show_spinner=False)
def naver_trend_daily(market: str) -> dict:
    """일별 투자자 매매동향 (영웅문 [0784]와 비슷해요. market: KOSPI / KOSDAQ)"""
    candidates = [
        ("https://stock.naver.com/api/domestic/market/trend/daily",
         {"marketType": market, "page": 1, "pageSize": 20}),
        (f"https://stock.naver.com/api/domestic/market/trend/daily/{market}", {"page": 1, "pageSize": 20}),
        ("https://m.stock.naver.com/api/domestic/market/trend/daily",
         {"marketType": market, "page": 1, "pageSize": 20}),
    ]
    errors = []
    for url, params in candidates:
        try:
            data = get_json(url, params)
            content = data.get("content") if isinstance(data, dict) else None
            if content:
                return {"rows": content, "url": url, "params": params, "errors": errors}
            errors.append(f"{url} {params} → 빈 응답")
        except Exception as e:
            errors.append(f"{url} {params} → {e}")
    return {"rows": [], "url": None, "params": None, "errors": errors}


INSTITUTION_CODES = {"1000", "2000", "3000", "3100", "4000", "5000", "6000"}


def parse_trend_row(row: dict) -> dict:
    sums = {"개인": 0.0, "외국인": 0.0, "기관": 0.0}
    for item in row.get("netAmounts") or []:
        gubun = str(pick(item, "investorGubun", "investorCode", "code"))
        amount = to_num(pick(item, "netAmount", "amount", "value", "netBuyAmount", "netTradeAmount"))
        if gubun == "8000":
            sums["개인"] += amount
        elif gubun in ("9000", "9001"):
            sums["외국인"] += amount
        elif gubun in INSTITUTION_CODES:
            sums["기관"] += amount
    sums["time"] = str(pick(row, "time", "localTradedAt", "bizdate"))
    return sums


# ---------------------------------------------------------------------
# 야후 파이낸스 (미국 지수·선물·유가, 약 10~15분 지연)
# ---------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def yf_quote(symbol: str) -> dict | None:
    try:
        fi = yf.Ticker(symbol).fast_info
        last, prev = float(fi.last_price), float(fi.previous_close)
        change = last - prev
        return {"price": last, "change": change, "rate": change / prev * 100 if prev else 0}
    except Exception:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def yf_detail(symbol: str) -> dict | None:
    try:
        t = yf.Ticker(symbol)
        fi = t.fast_info
        prev = float(fi.previous_close)
        h = t.history(period="1d", interval="1m")
        if h.empty:
            last, hi, lo, ts = float(fi.last_price), float(fi.day_high), float(fi.day_low), None
        else:
            last = float(h["Close"].iloc[-1])
            hi, lo, ts = float(h["High"].max()), float(h["Low"].min()), h.index[-1]
        change = last - prev
        return {"price": last, "high": hi, "low": lo, "change": change,
                "rate": change / prev * 100 if prev else 0, "time": kst_str(ts) if ts is not None else ""}
    except Exception:
        return None


@st.cache_data(ttl=10, show_spinner=False)
def upbit_btc() -> dict | None:
    try:
        r = requests.get("https://api.upbit.com/v1/ticker", params={"markets": "KRW-BTC"}, timeout=5)
        d = r.json()[0]
        return {"price": d["trade_price"], "change": d["signed_change_price"], "rate": d["signed_change_rate"] * 100}
    except Exception:
        return None


# ---------------------------------------------------------------------
# 키움 REST API (조회 전용)
# ---------------------------------------------------------------------
class KiwoomError(Exception):
    pass


def kiwoom_keys():
    try:
        k = st.secrets["kiwoom"]
        return str(k["app_key"]).strip(), str(k["app_secret"]).strip()
    except Exception:
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def kiwoom_token(app_key: str, app_secret: str) -> str:
    r = requests.post(f"{KIWOOM_HOST}/oauth2/token",
                      json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
                      headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=10)
    try:
        data = r.json()
    except ValueError:
        raise KiwoomError(f"토큰 발급 실패 (HTTP {r.status_code})")
    if not data.get("token"):
        raise KiwoomError(f"토큰 발급 실패: {data.get('return_msg') or data}")
    return data["token"]


def kiwoom_request(path: str, api_id: str, body: dict, cont_yn: str = "N", next_key: str = ""):
    keys = kiwoom_keys()
    if not keys:
        raise KiwoomError("키움 API 키가 설정되지 않았어요.")
    for attempt in range(2):
        token = kiwoom_token(*keys)
        r = requests.post(KIWOOM_HOST + path, json=body, timeout=10, headers={
            "Content-Type": "application/json;charset=UTF-8", "authorization": f"Bearer {token}",
            "api-id": api_id, "cont-yn": cont_yn, "next-key": next_key})
        try:
            data = r.json()
        except ValueError:
            raise KiwoomError(f"응답을 읽지 못했어요 (HTTP {r.status_code})")
        code = str(data.get("return_code", "0"))
        if code == "0":
            return data, r.headers.get("cont-yn", "N"), r.headers.get("next-key", "")
        msg = str(data.get("return_msg", ""))
        if attempt == 0 and (r.status_code == 401 or "토큰" in msg or "token" in msg.lower()):
            kiwoom_token.clear()
            continue
        raise KiwoomError(f"{msg} (코드 {code})")
    raise KiwoomError("요청에 실패했어요.")


def clean_code(code) -> str:
    c = str(code).split("_")[0]
    return c[1:] if c.startswith("A") and c[1:].isdigit() else c


def is_fund_name(name: str) -> bool:
    up = name.upper()
    return any(k.upper() in up for k in KR_FUND_KEYWORDS)


@st.cache_data(ttl=RANK_REFRESH, show_spinner=False)
def fetch_trade_value_pages(mrkt_tp: str, max_pages: int, min_eok: float) -> list:
    """거래대금상위(ka10032)를 페이지째 받아서 ETF/ETN류를 뺀 개별종목만 돌려줘요."""
    threshold = min_eok / TRADE_VALUE_TO_EOK
    rows, cont, nkey = [], "N", ""
    for _ in range(max_pages):
        data, cont, nkey = kiwoom_request("/api/dostk/rkinfo", "ka10032",
                                          {"mrkt_tp": mrkt_tp, "mang_stk_incls": "0", "stex_tp": "3"},
                                          cont_yn="Y" if nkey else "N", next_key=nkey)
        page = first_list(data)
        for it in page:
            name = str(pick(it, "stk_nm"))
            if is_fund_name(name):
                continue
            rows.append({"name": name, "code": clean_code(pick(it, "stk_cd")),
                         "price": abs(to_num(pick(it, "cur_prc"))), "rate": to_num(pick(it, "flu_rt")),
                         "value": to_num(pick(it, "trde_prica", "trde_amt"))})
        if not page or (page and to_num(pick(page[-1], "trde_prica", "trde_amt")) < threshold) or cont != "Y" or not nkey:
            break
        time.sleep(0.3)
    return [r for r in rows if r["value"] >= threshold]


def get_trade_value_rank(mrkt_tp: str) -> list:
    return fetch_trade_value_pages(mrkt_tp, 5, TRADE_VALUE_MIN_EOK)


def get_limit_movers() -> list:
    """거래대금 상위권(약 500억 이상, 최대 6페이지) 안에서 상한가·하한가 종목을 찾아요. (참고용, get_limit_rank가 더 정확해요)"""
    rows = fetch_trade_value_pages("000", 6, 500)
    return [r for r in rows if abs(r["rate"]) >= LIMIT_RATE]


@st.cache_data(ttl=RANK_REFRESH, show_spinner=False)
def get_limit_rank(sort_tp: str) -> list:
    """전일대비등락률상위(ka10027)를 상한(7)/하한(8) 정렬로 요청해요. 영웅문 [0162]와 같은 데이터예요."""
    body = {"mrkt_tp": "000", "sort_tp": sort_tp, "trde_qty_cnd": "0000", "stk_cnd": "0",
            "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0", "trde_prica_cnd": "0", "stex_tp": "3"}
    data, _, _ = kiwoom_request("/api/dostk/rkinfo", "ka10027", body)
    rows = []
    for it in first_list(data)[:50]:
        name = str(pick(it, "stk_nm"))
        if is_fund_name(name):
            continue
        rows.append({"name": name, "code": clean_code(pick(it, "stk_cd")),
                     "price": abs(to_num(pick(it, "cur_prc"))), "rate": to_num(pick(it, "flu_rt")),
                     "value": to_num(pick(it, "now_trde_qty", "trde_qty", "trde_prica"))})
    return rows


@st.cache_data(ttl=RANK_REFRESH, show_spinner=False)
def get_view_rank(qry_tp: str) -> list:
    """실시간종목조회순위(ka00198)"""
    data, _, _ = kiwoom_request("/api/dostk/stkinfo", "ka00198", {"qry_tp": qry_tp})
    rows = []
    for it in first_list(data)[:30]:
        rows.append({"name": str(pick(it, "stk_nm")), "code": clean_code(pick(it, "stk_cd")),
                     "rank": str(pick(it, "bigd_rank")), "move": abs(to_num(pick(it, "rank_chg"))),
                     "move_sign": str(pick(it, "rank_chg_sign")),
                     "price": abs(to_num(pick(it, "past_curr_prc", "cur_prc"))),
                     "rate": to_num(pick(it, "base_comp_chgr", "flu_rt"))})
    return rows


@st.cache_data(ttl=WATCH_REFRESH, show_spinner=False)
def get_program_daily(codes: tuple, date_str: str) -> dict:
    """종목일별프로그램매매추이(ka90013) = 영웅문 [0778] 화면과 같은 데이터"""
    out = {}
    for code in codes:
        try:
            data, _, _ = kiwoom_request("/api/dostk/mrkcond", "ka90013",
                                        {"amt_qty_tp": "2", "stk_cd": code, "date": date_str})
            out[code] = {"rows": first_list(data), "error": None}
        except Exception as e:
            out[code] = {"rows": [], "error": str(e)}
        time.sleep(0.35)
    return out


def parse_program_daily(r: dict) -> dict:
    """필드 이름이 정확히 확인되지 않아, 수량(qty)과 금액(amt) 후보를 모두 계산해 값이 있는 쪽을 써요."""
    qty_buy = abs(to_num(pick(r, "prm_buy_qty", "buy_qty", "prm_sell_smtm_qty")))
    qty_sell = abs(to_num(pick(r, "prm_sell_qty", "sell_qty")))
    qty_net_raw = pick(r, "prm_netprps_qty", "netprps_qty", "prm_netprps_smtm_qty")
    qty_net = to_num(qty_net_raw) if qty_net_raw != "" else qty_buy - qty_sell

    amt_buy = abs(to_num(pick(r, "prm_buy_amt", "buy_amt")))
    amt_sell = abs(to_num(pick(r, "prm_sell_amt", "sell_amt")))
    amt_net_raw = pick(r, "prm_netprps_amt", "netprps_amt")
    amt_net = to_num(amt_net_raw) if amt_net_raw != "" else amt_buy - amt_sell

    dt = str(pick(r, "dt", "date", "base_dt"))
    if qty_buy or qty_sell or qty_net:
        return {"dt": dt, "buy": qty_buy, "sell": qty_sell, "net": qty_net, "unit": "주"}
    return {"dt": dt, "buy": amt_buy, "sell": amt_sell, "net": amt_net, "unit": "백만"}


def latest_program(rows: list, today: str = "") -> dict | None:
    parsed = [parse_program_daily(r) for r in rows]
    if not parsed:
        return None
    if today:
        for p in parsed:
            if p["dt"][:8] == today:
                return p
    return max(parsed, key=lambda x: x["dt"]) if parsed[0]["dt"] else parsed[0]


@st.cache_data(ttl=WATCH_REFRESH, show_spinner=False)
def get_investor_by_stock(code: str) -> dict:
    """종목별투자자기관별차트요청(ka10060) = 영웅문 [0796] 종목별투자자 화면과 같은 데이터"""
    try:
        data, _, _ = kiwoom_request("/api/dostk/stkinfo", "ka10060",
                                    {"stk_cd": code, "dt": datetime.now(KST).strftime("%Y%m%d"),
                                     "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000"})
        return {"rows": first_list(data), "error": None}
    except Exception as e:
        return {"rows": [], "error": str(e)}


def parse_investor_row(r: dict) -> dict:
    return {"dt": str(pick(r, "dt", "date")),
            "개인": to_num(pick(r, "ind_invsr", "individual")),
            "외국인": to_num(pick(r, "frgnr_invsr", "foreign")),
            "기관": to_num(pick(r, "orgn", "orgn_smtm", "institution"))}


# ---------------------------------------------------------------------
# 탭 1: 지수
# ---------------------------------------------------------------------
INDEX_INFO = {"KOSPI": ("코스피", "^KS11"), "KOSDAQ": ("코스닥", "^KQ11")}


def inv_line(label: str, values: dict | None) -> str:
    if not values:
        return f'<div class="inv-line"><span class="lbl">{label}</span><span>데이터를 불러오지 못했어요</span></div>'
    cells = "".join(f'<span>{who}<b class="{sign_cls(values.get(who, 0))}">{eok(values.get(who, 0))}</b></span>'
                    for who in ("외국인", "기관", "개인"))
    return f'<div class="inv-line"><span class="lbl">{label}</span>{cells}</div>'


def index_header_html(name: str, q: dict | None) -> str:
    if not q:
        return f'<div class="tx"><div class="ix-name">{name}</div><div class="empty">지수를 불러오지 못했어요</div></div>'
    cls = sign_cls(q["change"])
    status = "장중" if q.get("status") == "OPEN" else "장마감"
    return (f'<div class="tx"><div class="ix-head"><div class="ix-name">{name}</div>'
            f'<div class="ix-time">{status} {kst_str(q["time"]) if q.get("time") else ""}</div></div>'
            f'<div class="ix-row"><div class="ix-val {cls}">{q["price"]:,.2f}</div>'
            f'<div class="chg {cls}">{signed(q["change"])} ({signed(q["rate"])}%)</div></div></div>')


@st.cache_data(ttl=600, show_spinner=False)
def index_history(symbol: str, period_key: str) -> pd.DataFrame:
    cfg = {"일봉": ("1y", "1d", 130), "주봉": ("3y", "1wk", 100), "월봉": ("10y", "1mo", 80), "연봉": ("max", "1mo", 30)}
    period, interval, keep = cfg[period_key]
    df = yf.Ticker(symbol).history(period=period, interval=interval)[["Open", "High", "Low", "Close"]].dropna()
    if period_key == "연봉":
        df = df.resample("YE").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    ma_windows = [] if period_key == "연봉" else [5, 20, 60, 120]
    for w in ma_windows:
        df[f"ma{w}"] = df["Close"].rolling(w).mean()
    df = df.tail(keep).reset_index()
    df.columns = ["date", "open", "high", "low", "close"] + [f"ma{w}" for w in ma_windows]
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_convert(None)
    return df


MA_COLORS = {"ma5": "#FFB400", "ma20": "#F04452", "ma60": "#7B61FF", "ma120": "#00B868"}


def candle_chart(df: pd.DataFrame, period_key: str) -> alt.Chart:
    fmt = {"일봉": "%-m/%-d", "주봉": "%y/%-m", "월봉": "%Y", "연봉": "%Y"}[period_key]
    width = max(2, min(10, int(300 / max(len(df), 1) * 0.6)))
    color = alt.condition("datum.close >= datum.open", alt.value(UP), alt.value(DOWN))
    base = alt.Chart(df).encode(
        x=alt.X("date:T", axis=alt.Axis(title=None, format=fmt, labelColor=FLAT, grid=False, domain=False,
                                        ticks=False, tickCount=5, labelFontSize=11)))
    y = alt.Y("low:Q", scale=alt.Scale(zero=False), axis=alt.Axis(title=None, orient="right", format=",.0f",
              labelColor=FLAT, gridColor="#F2F4F6", domain=False, ticks=False, tickCount=4, labelFontSize=11))
    wick = base.mark_rule(strokeWidth=1).encode(
        y=y, y2="high:Q", color=color,
        tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                 alt.Tooltip("open:Q", title="시가", format=",.2f"), alt.Tooltip("high:Q", title="고가", format=",.2f"),
                 alt.Tooltip("low:Q", title="저가", format=",.2f"), alt.Tooltip("close:Q", title="종가", format=",.2f")])
    body = base.mark_bar(size=width).encode(y="open:Q", y2="close:Q", color=color)
    chart = wick + body
    for key, hexcolor in MA_COLORS.items():
        if key in df.columns and df[key].notna().any():
            chart += base.mark_line(strokeWidth=1.3, color=hexcolor, opacity=0.9, interpolate="monotone").encode(
                y=alt.Y(f"{key}:Q", scale=alt.Scale(zero=False)))
    return chart.properties(height=190, width="container", background="transparent").configure_view(strokeWidth=0)


def ma_legend_html(df: pd.DataFrame) -> str:
    labels = {"ma5": "5", "ma20": "20", "ma60": "60", "ma120": "120"}
    chips = "".join(f'<span><i style="background:{c}"></i>{labels[k]}일</span>'
                    for k, c in MA_COLORS.items() if k in df.columns and df[k].notna().any())
    return f'<div class="ma-legend">{chips}</div>' if chips else ""


# ---------------------------------------------------------------------
# 실시간 틱 버퍼: 1분/3분봉은 서버가 폴링하면서 쌓은 데이터로 그려요.
# (서버를 재시작하면 버퍼가 비어서 몇 분간 차트가 비어 있을 수 있어요)
# ---------------------------------------------------------------------
TICK_KEEP_MIN = 90


def record_tick(code: str, price: float) -> None:
    if not price:
        return
    buf = st.session_state.setdefault("_tick_buffers", {})
    series = buf.setdefault(code, [])
    now = datetime.now(KST)
    if series and (now - series[-1][0]).total_seconds() < 4:
        series[-1] = (now, price)
    else:
        series.append((now, price))
    cutoff = now - timedelta(minutes=TICK_KEEP_MIN)
    while series and series[0][0] < cutoff:
        series.pop(0)


def get_tick_df(code: str) -> pd.DataFrame:
    series = st.session_state.get("_tick_buffers", {}).get(code, [])
    if not series:
        return pd.DataFrame(columns=["date", "price"])
    return pd.DataFrame(series, columns=["date", "price"])


def intraday_chart(df: pd.DataFrame, minutes: int) -> alt.Chart | None:
    if df.empty:
        return None
    d = df.set_index("date").resample(f"{minutes}min")["price"].ohlc().dropna().reset_index()
    if d.empty:
        return None
    color = UP if d["close"].iloc[-1] >= d["open"].iloc[0] else DOWN
    base = alt.Chart(d).encode(
        x=alt.X("date:T", axis=alt.Axis(title=None, format="%H:%M", labelColor=FLAT, grid=False, domain=False,
                                        ticks=False, tickCount=5, labelFontSize=11)))
    y = alt.Y("low:Q", scale=alt.Scale(zero=False), axis=alt.Axis(title=None, orient="right", format=",.0f",
              labelColor=FLAT, gridColor="#F2F4F6", domain=False, ticks=False, tickCount=4, labelFontSize=11))
    width = max(3, min(14, int(280 / max(len(d), 1))))
    cond = alt.condition("datum.close >= datum.open", alt.value(UP), alt.value(DOWN))
    wick = base.mark_rule(strokeWidth=1).encode(y=y, y2="high:Q", color=cond,
        tooltip=[alt.Tooltip("date:T", title="시간", format="%H:%M"),
                 alt.Tooltip("open:Q", title="시가", format=",.2f"), alt.Tooltip("close:Q", title="종가", format=",.2f")])
    body = base.mark_bar(size=width).encode(y="open:Q", y2="close:Q", color=cond)
    return (wick + body).properties(height=190, width="container", background="transparent").configure_view(strokeWidth=0)


def mini_card(name: str, q: dict | None, fmt=lambda v: f"{v:,.2f}") -> str:
    if not q:
        return f'<div class="mini"><div class="n">{name}</div><div class="p">-</div></div>'
    cls = sign_cls(q["change"])
    return (f'<div class="mini"><div class="n">{name}</div><div class="p">{fmt(q["price"])}</div>'
            f'<div class="chg {cls}">{signed(q["rate"])}%</div></div>')


def simple_line(name: str, q: dict | None, fmt=lambda v: f"{v:,.2f}") -> str:
    if not q:
        return f'<div class="row-simple"><span class="n">{name}</span><span class="p">-</span></div>'
    cls = sign_cls(q["change"])
    return (f'<div class="row-simple"><span class="n">{name}</span><span class="r">'
            f'<span class="p">{fmt(q["price"])}</span><span class="chg {cls}">{signed(q["rate"])}%</span></span></div>')


def detail_card(name: str, d: dict | None, note: str = "") -> str:
    if not d:
        return f'<div class="tx card"><div class="ix-name">{name}</div><div class="empty">불러오지 못했어요</div></div>'
    cls = sign_cls(d["change"])
    return (f'<div class="tx card"><div class="ix-head"><div class="ix-name">{name}</div>'
            f'<div class="ix-time">{html.escape(d["time"])} {note}</div></div>'
            f'<div class="ix-val {cls}">{d["price"]:,.2f}</div>'
            f'<div class="chg {cls}">{signed(d["change"])} ({signed(d["rate"])}%)</div>'
            f'<div class="detail"><div><div class="k">고가</div><div class="v">{d["high"]:,.2f}</div></div>'
            f'<div><div class="k">저가</div><div class="v">{d["low"]:,.2f}</div></div>'
            f'<div><div class="k">변동</div><div class="v {cls}">{signed(d["change"])}</div></div></div></div>')


def investor_chart(rows: list) -> alt.Chart | None:
    parsed = [parse_trend_row(r) for r in rows]
    df = pd.DataFrame(parsed)
    if df.empty:
        return None
    df = df[df["time"].str.len() >= 4].copy()
    t = df["time"].str.replace(":", "", regex=False).str[:4]
    df["t"] = pd.to_datetime(t, format="%H%M", errors="coerce")
    df = df.dropna(subset=["t"]).sort_values("t")
    if df.empty:
        return None
    long = df.melt(id_vars="t", value_vars=["외국인", "기관", "개인"], var_name="투자자", value_name="순매수")
    return (alt.Chart(long).mark_line(strokeWidth=2.2, interpolate="monotone").encode(
        x=alt.X("t:T", axis=alt.Axis(title=None, format="%H:%M", labelColor=FLAT, grid=False, domain=False,
                                     ticks=False, tickCount=5, labelFontSize=11)),
        y=alt.Y("순매수:Q", axis=alt.Axis(title=None, orient="right", format="~s", labelColor=FLAT,
                                        gridColor="#F2F4F6", domain=False, ticks=False, tickCount=4, labelFontSize=11)),
        color=alt.Color("투자자:N", scale=alt.Scale(domain=list(INVESTOR_COLORS), range=list(INVESTOR_COLORS.values())),
                        legend=alt.Legend(orient="top", title=None, labelFontSize=12)),
        tooltip=[alt.Tooltip("t:T", title="시간", format="%H:%M"), "투자자:N",
                 alt.Tooltip("순매수:Q", format=",.0f")],
    ).properties(height=220, width="container", background="transparent").configure_view(strokeWidth=0))


@st.fragment(run_every=INDEX_REFRESH)
def index_header(code: str) -> None:
    try:
        q = naver_index(code)
    except Exception:
        q = None
    if q and q.get("price"):
        record_tick(code, q["price"])
    show(index_header_html(INDEX_INFO[code][0], q))


@st.fragment(run_every=10)
def index_candle(code: str) -> None:
    period = st.segmented_control("차트", ["1분", "3분", "일봉", "주봉", "월봉", "연봉"], default="일봉",
                                  key=f"candle_{code}", label_visibility="collapsed") or "일봉"
    if period in ("1분", "3분"):
        df = get_tick_df(code)
        chart = intraday_chart(df, 1 if period == "1분" else 3)
        if chart is None:
            show('<div class="tx empty">지금부터 쌓이는 실시간 데이터로 그려요. 잠시 후 다시 확인해 주세요.</div>')
        else:
            st.altair_chart(chart, theme=None)
            show('<div class="tx inv-note">서버가 켜진 뒤부터 쌓인 실시간 데이터예요. 재시작하면 초기화돼요.</div>')
        return
    try:
        df = index_history(INDEX_INFO[code][1], period)
    except Exception:
        df = None
    if df is None or df.empty:
        show('<div class="tx empty">차트를 불러오지 못했어요</div>')
        return
    st.altair_chart(candle_chart(df, period), theme=None)
    show(ma_legend_html(df))


def spot_investors(code: str) -> dict | None:
    investors = parse_deal_trend(naver_index_integration(code)["data"])
    if not investors:
        trend = naver_trend_time(code)
        if trend["rows"]:
            latest = parse_trend_row(trend["rows"][0])
            investors = {k: latest[k] for k in ("개인", "외국인", "기관")}
    return investors


def futures_investors() -> dict | None:
    fut = naver_trend_time("FUT")
    if fut["rows"]:
        latest = parse_trend_row(fut["rows"][0])
        return {k: latest[k] for k in ("개인", "외국인", "기관")}
    return None


@st.fragment(run_every=INDEX_REFRESH)
def index_investors(code: str) -> None:
    lines = inv_line("현물", spot_investors(code))
    if code == "KOSPI":
        lines += inv_line("선물", futures_investors())
    else:
        lines += '<div class="inv-note">코스닥150 선물 투자자 동향은 무료로 받을 수 있는 곳이 없어 표시하지 못해요</div>'
    show(f'<div class="tx">{lines}</div>')


@st.fragment(run_every=60)
def investor_chart_section() -> None:
    show('<div class="tx section-title">투자자 순매수 흐름</div>')
    with st.container(border=True):
        target = st.segmented_control("시장", ["코스피", "코스닥", "선물"], default="코스피", key="inv_chart",
                                      label_visibility="collapsed") or "코스피"
        trend = naver_trend_time({"코스피": "KOSPI", "코스닥": "KOSDAQ", "선물": "FUT"}[target])
        chart = investor_chart(trend["rows"]) if trend["rows"] else None
        if chart is not None:
            st.altair_chart(chart, theme=None)
        else:
            show('<div class="tx empty">오늘의 시간별 데이터가 아직 없어요. 장중에 표시돼요.</div>')


@st.fragment(run_every=300)
def daily_investor_section() -> None:
    """영웅문 [0784] 일별동향과 비슷하게, 최근 며칠 시장 전체 수급을 보여줘요."""
    show('<div class="tx section-title">일별 투자자 동향</div>')
    with st.container(border=True):
        target = st.segmented_control("시장", ["코스피", "코스닥"], default="코스피", key="inv_daily",
                                      label_visibility="collapsed") or "코스피"
        daily = naver_trend_daily({"코스피": "KOSPI", "코스닥": "KOSDAQ"}[target])
        if not daily["rows"]:
            show('<div class="tx empty">일별 데이터를 아직 못 받아왔어요.</div>')
        else:
            parsed = [parse_trend_row(r) for r in daily["rows"]][:10]
            lines = []
            for p in parsed:
                dt = p["time"]
                label = fmt_dt(dt) if dt and dt[:8].isdigit() else str(dt)
                cells = "".join(f'<span>{who}<b class="{sign_cls(p[who])}">{eok(p[who])}</b></span>'
                                for who in ("외국인", "기관", "개인"))
                lines.append(f'<div class="inv-line"><span class="lbl">{label}</span>{cells}</div>')
            show(f'<div class="tx">{"".join(lines)}</div>')
        with st.expander("문제 해결용 정보"):
            show_url = daily["url"] or daily["errors"]
            st.write(show_url)
            if daily["rows"]:
                st.write("첫 줄:", daily["rows"][0])


@st.fragment(run_every=30)
def global_section() -> None:
    usdkrw_html = mini_card("원/달러", yf_quote("KRW=X"))
    btc_html = mini_card("비트코인", upbit_btc(), lambda v: "{:,.0f}만원".format(v / 10000))
    show(f'<div class="tx mini-grid" style="margin-top:4px">{usdkrw_html}{btc_html}</div>')

    # 뉴욕 증시
    show('<div class="tx section-title">뉴욕 증시</div>')
    show(f'<div class="tx mini-grid">{mini_card("S&amp;P 500", yf_quote("^GSPC"))}'
         f'{mini_card("나스닥", yf_quote("^IXIC"))}{mini_card("VIX", yf_quote("^VIX"))}</div>')
    st.write("")
    # US Tech 100: 인베스팅닷컴의 'Derived' 값은 나스닥100 현물이 아니라 선물(NQ) 기준이에요.
    # NQ=F는 야후가 자동으로 최근월물을 따라가 주는 연속 선물 티커예요.
    show(detail_card("US Tech 100 (나스닥100 선물)", yf_detail("NQ=F"), "· 인베스팅 Derived 값 기준 · 약 10분 지연"))

    # 국제 유가 (한 줄씩, 간결하게)
    show('<div class="tx section-title">국제 유가 (선물)</div>')
    oil_html = simple_line("WTI", yf_quote("CL=F"), lambda v: f"&#36;{v:,.2f}") + \
               simple_line("브렌트유", yf_quote("BZ=F"), lambda v: f"&#36;{v:,.2f}")
    show(f'<div class="tx card">{oil_html}<div class="inv-note">약 10분 지연</div></div>')

    with st.expander("문제 해결용 정보"):
        diag = {}
        for code in ("KOSPI", "KOSDAQ"):
            integ = naver_index_integration(code)
            diag[f"{code} integration"] = integ["url"] or integ["errors"]
            if isinstance(integ["data"], dict):
                diag[f"{code} dealTrendInfo"] = integ["data"].get("dealTrendInfo")
        fut = naver_trend_time("FUT")
        diag["선물 동향"] = fut["url"] or fut["errors"]
        if fut["rows"]:
            diag["선물 첫 줄"] = fut["rows"][0]
        st.write(diag)


# ---------------------------------------------------------------------
# 탭 2: 거래대금
# ---------------------------------------------------------------------
def rate_text(rate: float) -> tuple:
    return (f"{'+' if rate > 0 else ''}{rate:.2f}%", sign_cls(rate))


def trade_value_chart(rows: list) -> alt.Chart:
    df = pd.DataFrame(rows[:10])
    df["eok"] = df["value"] * TRADE_VALUE_TO_EOK
    base = alt.Chart(df).encode(
        y=alt.Y("name:N", sort="-x", axis=alt.Axis(title=None, domain=False, ticks=False,
                                                labelColor="#4E5968", labelFontSize=12, labelPadding=8)),
        x=alt.X("eok:Q", axis=None))
    bars = base.mark_bar(cornerRadiusEnd=6, height=16).encode(
        color=alt.condition(alt.datum.rate > 0, alt.value(UP), alt.value(DOWN)),
        tooltip=[alt.Tooltip("name:N", title="종목"), alt.Tooltip("eok:Q", title="거래대금(억)", format=",.0f"),
                 alt.Tooltip("rate:Q", title="등락률(%)", format="+.2f")])
    labels = base.mark_text(align="left", dx=6, color=FLAT, fontSize=11).encode(text=alt.Text("eok:Q", format=",.0f"))
    return (bars + labels).properties(height=len(df) * 30, width="container", background="transparent") \
        .configure_view(strokeWidth=0)


def stock_row(rank: str, r: dict, sub: str, move: str = "") -> str:
    rt, cls = rate_text(r["rate"])
    return (f'<a class="row" href="{news_url(r["code"])}" target="_blank" rel="noopener">'
            f'<div class="rank-no">{html.escape(rank)}</div>{avatar(r["name"])}'
            f'<div class="row-main"><div class="row-name">{html.escape(r["name"])}{move}</div>'
            f'<div class="row-sub">{sub}</div></div>'
            f'<div class="row-right"><div class="row-price">{won(r["price"])}</div><div class="chg {cls}">{rt}</div></div></a>')


def need_kiwoom() -> bool:
    if kiwoom_keys():
        return True
    show('<div class="tx card"><div class="card-title">키움 API 연결이 필요해요</div>'
         '<div class="empty">키움 키를 설정한 서버에서 실행하면 표시돼요.</div></div>')
    return False


@st.fragment(run_every=RANK_REFRESH)
def trade_value_section() -> None:
    if not need_kiwoom():
        return
    now = datetime.now(KST)
    show(f'<div class="tx updated">{now:%H:%M:%S} 기준, {RANK_REFRESH}초마다 갱신돼요</div>')
    c1, c2 = st.columns(2)
    market = c1.segmented_control("시장", ["전체", "코스피", "코스닥"], default="전체", key="tv_market",
                                  label_visibility="collapsed") or "전체"
    sort_by = c2.segmented_control("정렬", ["거래대금순", "등락률순"], default="거래대금순", key="tv_sort",
                                   label_visibility="collapsed") or "거래대금순"
    try:
        rows = get_trade_value_rank({"전체": "000", "코스피": "001", "코스닥": "101"}[market])
    except Exception as e:
        show(f'<div class="tx card"><div class="empty">거래대금 순위를 불러오지 못했어요: {html.escape(str(e))}</div></div>')
        return
    if not rows:
        show(f'<div class="tx card"><div class="empty">거래대금 {TRADE_VALUE_MIN_EOK:,}억 이상인 종목이 아직 없어요.</div></div>')
        return
    if sort_by == "등락률순":
        rows = sorted(rows, key=lambda r: -r["rate"])
    show(f'<div class="tx section-title">거래대금 {TRADE_VALUE_MIN_EOK:,}억 이상 · {len(rows)}종목</div>')
    with st.container(border=True):
        st.altair_chart(trade_value_chart(rows[:10] if sort_by == "거래대금순" else sorted(rows, key=lambda r: -r["value"])[:10]),
                        theme=None)
    show('<div class="tx tap-hint">종목을 누르면 해당 종목 뉴스가 열려요</div>')
    items = "".join(stock_row(str(i), r, f"거래대금 {r['value'] * TRADE_VALUE_TO_EOK:,.0f}억")
                    for i, r in enumerate(rows, start=1))
    show(f'<div class="tx card list">{items}</div>')

    if edit_pin():
        with st.expander("+ 이 목록에서 관심종목 추가"):
            if pin_gate("tv_add"):
                names = [f'{r["name"]} ({r["code"]})' for r in rows]
                pick_i = st.selectbox("종목 선택", range(len(rows)), format_func=lambda i: names[i], key="tv_add_pick")
                if st.button("관심종목에 추가", key="tv_add_btn"):
                    wl = load_watchlist()
                    r = rows[pick_i]
                    if all(x["code"] != r["code"] for x in wl["kr"]):
                        wl["kr"].append({"name": r["name"], "code": r["code"], "market": "KS"})
                        write_json(WATCHLIST_FILE, wl)
                    st.success(f'{r["name"]} 추가했어요.')


# ---------------------------------------------------------------------
# 탭: 상한가·하한가
# ---------------------------------------------------------------------
@st.fragment(run_every=RANK_REFRESH)
def limit_section() -> None:
    if not need_kiwoom():
        return
    now = datetime.now(KST)
    show(f'<div class="tx updated">{now:%H:%M:%S} 기준, {RANK_REFRESH}초마다 갱신돼요</div>')

    ups, downs, used_fallback, err = [], [], False, None
    try:
        ups = [r for r in get_limit_rank("7") if r["rate"] > 0]
        downs = [r for r in get_limit_rank("8") if r["rate"] < 0]
    except Exception as e:
        err = str(e)
        used_fallback = True
        try:
            rows = get_limit_movers()
            ups = sorted([r for r in rows if r["rate"] > 0], key=lambda r: -r["rate"])
            downs = sorted([r for r in rows if r["rate"] < 0], key=lambda r: r["rate"])
        except Exception as e2:
            show(f'<div class="tx card"><div class="empty">불러오지 못했어요: {html.escape(str(e2))}</div></div>')
            return

    if used_fallback:
        show(f'<div class="tx inv-note">영웅문 [0162]와 같은 방식(ka10027) 요청이 실패해서, '
             f'거래대금 상위권에서 찾는 방식으로 대신 보여드려요. 오류: {html.escape(err or "")}</div>')

    show(f'<div class="tx section-title">상한가 · {len(ups)}종목</div>')
    if ups:
        show(f'<div class="tx card list">{"".join(stock_row(str(i), r, r["code"]) for i, r in enumerate(ups, 1))}</div>')
    else:
        show('<div class="tx card"><div class="empty">상한가 종목이 없어요.</div></div>')
    show(f'<div class="tx section-title">하한가 · {len(downs)}종목</div>')
    if downs:
        show(f'<div class="tx card list">{"".join(stock_row(str(i), r, r["code"]) for i, r in enumerate(downs, 1))}</div>')
    else:
        show('<div class="tx card"><div class="empty">하한가 종목이 없어요.</div></div>')

    with st.expander("문제 해결용 정보"):
        if not used_fallback:
            st.write("ka10027 방식으로 정상 조회됐어요.")
        try:
            raw, _, _ = kiwoom_request("/api/dostk/rkinfo", "ka10027",
                                       {"mrkt_tp": "000", "sort_tp": "7", "trde_qty_cnd": "0000", "stk_cnd": "0",
                                        "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0",
                                        "trde_prica_cnd": "0", "stex_tp": "3"})
            rows_raw = first_list(raw)
            if rows_raw:
                st.write("응답 항목 이름:", list(rows_raw[0].keys()))
                st.write("첫 줄:", rows_raw[0])
            else:
                st.write("빈 응답이 왔어요 (지금 상한가 종목이 없을 수도 있어요).")
        except Exception as e:
            st.write("진단 호출 오류:", str(e))


# ---------------------------------------------------------------------
# 탭 3: 조회순위
# ---------------------------------------------------------------------
@st.fragment(run_every=RANK_REFRESH)
def view_rank_section() -> None:
    if not need_kiwoom():
        return
    now = datetime.now(KST)
    show(f'<div class="tx updated">{now:%H:%M:%S} 기준, {RANK_REFRESH}초마다 갱신돼요</div>')
    periods = {"30초": "5", "1분": "1", "10분": "2", "1시간": "3", "당일": "4"}
    period = st.segmented_control("기준", list(periods), default="1분", key="view_period",
                                  label_visibility="collapsed") or "1분"
    try:
        rows = get_view_rank(periods[period])
    except Exception as e:
        show(f'<div class="tx card"><div class="empty">조회순위를 불러오지 못했어요: {html.escape(str(e))}</div></div>')
        return
    if not rows:
        show('<div class="tx card"><div class="empty">표시할 데이터가 없어요.</div></div>')
        return
    show('<div class="tx tap-hint" style="padding-top:8px">종목을 누르면 해당 종목 뉴스가 열려요</div>')
    items = []
    for i, r in enumerate(rows, start=1):
        move = ""
        if r["move"] and r["move_sign"] in ("1", "2"):
            move = f'<span class="rank-move up">▲{r["move"]:.0f}</span>'
        elif r["move"] and r["move_sign"] in ("4", "5"):
            move = f'<span class="rank-move down">▼{r["move"]:.0f}</span>'
        items.append(stock_row(r["rank"] or str(i), r, r["code"], move))
    show(f'<div class="tx card list">{"".join(items)}</div>')


# ---------------------------------------------------------------------
# 탭 4: 관심
# ---------------------------------------------------------------------
def fmt_dt(dt: str) -> str:
    return f"{dt[4:6]}/{dt[6:8]}" if len(dt) >= 8 and dt[:8].isdigit() else dt


@st.cache_data(ttl=3600, show_spinner=False)
def us_search(query: str) -> list:
    """야후 파이낸스 검색으로 미국 종목명을 티커로 바꿔요."""
    try:
        data = get_json("https://query2.finance.yahoo.com/v1/finance/search",
                        {"q": query, "quotesCount": 8, "newsCount": 0})
    except Exception:
        return []
    us_exchanges = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BATS", "PNK", "NAS", "NYS"}
    out = []
    for q in data.get("quotes", []):
        if q.get("quoteType") == "EQUITY" and q.get("exchange") in us_exchanges and q.get("symbol"):
            out.append({"name": q.get("shortname") or q.get("longname") or q["symbol"], "ticker": q["symbol"]})
    return out


@st.cache_data(ttl=20, show_spinner=False)
def us_extended_quote(ticker: str) -> dict | None:
    """정규장 종가에 더해, 받아올 수 있으면 장전·장후 시세도 함께 줘요 (야후 제공 여부에 따라 없을 수 있어요)."""
    base = yf_quote(ticker)
    if not base:
        return None
    result = {"regular": base, "state": "", "extra": None}
    try:
        info = yf.Ticker(ticker).get_info()
        state = str(info.get("marketState", ""))
        result["state"] = state
        pre_p, post_p = info.get("preMarketPrice"), info.get("postMarketPrice")
        if state == "PRE" and pre_p:
            chg = float(pre_p) - base["price"]
            result["extra"] = {"label": "장전", "price": float(pre_p), "change": chg,
                               "rate": chg / base["price"] * 100 if base["price"] else 0}
        elif state in ("POST", "POSTPOST", "CLOSED") and post_p:
            chg = float(post_p) - base["price"]
            result["extra"] = {"label": "장후", "price": float(post_p), "change": chg,
                               "rate": chg / base["price"] * 100 if base["price"] else 0}
    except Exception:
        pass
    return result


def bulk_add_watchlist(text: str) -> tuple:
    """국내·미국 종목명을 섞어서 한 번에 넣을 수 있어요. 국내는 코드로도 가능해요."""
    tokens = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    wl = load_watchlist()
    added, missed = [], []
    for t in tokens:
        if t.isdigit() and len(t) == 6:
            if all(x["code"] != t for x in wl["kr"]):
                wl["kr"].append({"name": t, "code": t, "market": "KS"})
            added.append(t)
            continue
        try:
            kr_hits = naver_search(t)
        except Exception:
            kr_hits = []
        if kr_hits:
            item = kr_hits[0]
            if all(x["code"] != item["code"] for x in wl["kr"]):
                wl["kr"].append(item)
            added.append(item["name"])
            continue
        us_hits = us_search(t)
        if us_hits:
            item = us_hits[0]
            if all(x["ticker"] != item["ticker"] for x in wl["us"]):
                wl["us"].append({"name": item["name"], "ticker": item["ticker"]})
            added.append(f'{item["name"]}({item["ticker"]})')
            continue
        missed.append(t)
    write_json(WATCHLIST_FILE, wl)
    return added, missed


def watch_card(item: dict, q: dict | None, prog_rows: list, today: str) -> str:
    name, code = item["name"], item["code"]
    price_html = ""
    if q and q.get("price"):
        rt, cls = rate_text(q["rate"])
        price_html = f'<span class="num">{won(q["price"])}</span> <span class="chg {cls}">{rt}</span>'
    top = (f'<a class="prog-top" href="{news_url(code)}" target="_blank" rel="noopener">{avatar(name)}'
           f'<div class="row-main"><div class="row-name">{html.escape(name)}</div>'
           f'<div class="row-sub">{price_html or html.escape(code)}</div></div>')
    p = latest_program(prog_rows, today)
    if not p:
        return f'<div class="prog">{top}<div class="row-right"><div class="row-sub">프로그램 데이터 없음</div></div></a></div>'
    u = p["unit"]
    total = p["buy"] + p["sell"]
    buy_pct = p["buy"] / total * 100 if total else 50
    return (f'<div class="prog">{top}<div class="row-right"><div class="prog-net {sign_cls(p["net"])}">'
            f'{"+" if p["net"] > 0 else ""}{p["net"]:,.0f}{u}</div>'
            f'<div class="row-sub">프로그램 순매수 · {fmt_dt(p["dt"])}</div></div></a>'
            f'<div class="prog-bar"><div class="b" style="width:{buy_pct:.1f}%"></div>'
            f'<div class="s" style="width:{100 - buy_pct:.1f}%"></div></div>'
            f'<div class="prog-nums"><span>매수<b>{p["buy"]:,.0f}{u}</b></span>'
            f'<span>매도<b>{p["sell"]:,.0f}{u}</b></span></div></div>')


def us_row_html(it: dict) -> str:
    ext = us_extended_quote(it["ticker"])
    if not ext:
        right = '<div class="row-sub">-</div>'
        sub = html.escape(it["ticker"])
    else:
        base = ext["regular"]
        rt, cls = rate_text(base["rate"])
        right = f'<div class="row-price">{usd(base["price"])}</div><div class="chg {cls}">{rt}</div>'
        sub = f'{html.escape(it["ticker"])} · 정규장 종가'
        if ext["extra"]:
            ert, ecls = rate_text(ext["extra"]["rate"])
            right += (f'<div class="row-sub" style="margin-top:2px">{ext["extra"]["label"]} '
                     f'{usd(ext["extra"]["price"])} <span class="chg {ecls}">{ert}</span></div>')
    return (f'<div class="row">{avatar(it["name"], logo_us(it["ticker"]))}'
            f'<div class="row-main"><div class="row-name">{html.escape(it["name"])}</div>'
            f'<div class="row-sub">{sub}</div></div><div class="row-right">{right}</div></div>')


def investor_table_html(rows: list) -> str:
    parsed = [parse_investor_row(r) for r in rows if str(pick(r, "dt", "date"))]
    parsed = sorted(parsed, key=lambda x: x["dt"], reverse=True)[:10]
    if not parsed:
        return '<div class="empty">데이터가 없어요.</div>'
    lines = []
    for p in parsed:
        cells = "".join(f'<span>{who}<b class="{sign_cls(p[who])}">{eok(p[who] / 100)}</b></span>'
                        for who in ("개인", "외국인", "기관"))
        lines.append(f'<div class="inv-line"><span class="lbl">{fmt_dt(p["dt"])}</span>{cells}</div>')
    return "".join(lines)


def investor_by_stock_section() -> None:
    if not kiwoom_keys():
        return
    kr = load_watchlist()["kr"]
    if not kr:
        return
    show('<div class="tx section-title">종목별 투자자 매매동향</div>')
    with st.container(border=True):
        names = [i["name"] for i in kr]
        sel = st.segmented_control("종목", names, default=names[0], key="inv_stock_pick",
                                   label_visibility="collapsed") or names[0]
        code = next(i["code"] for i in kr if i["name"] == sel)
        result = get_investor_by_stock(code)
        if result["error"]:
            show(f'<div class="tx empty">불러오지 못했어요: {html.escape(result["error"])}</div>')
        else:
            show(f'<div class="tx">{investor_table_html(result["rows"])}</div>')
            show('<div class="tx inv-note">단위는 백만원(억원으로 환산해 표시)이에요. 최근 10일이에요.</div>')
        with st.expander("문제 해결용 정보"):
            if result["rows"]:
                st.write("항목 이름:", list(result["rows"][0].keys()))
                st.write("첫 줄:", result["rows"][0])
            elif not result["error"]:
                st.write("빈 응답이에요.")


@st.fragment(run_every=WATCH_REFRESH)
def watch_section() -> None:
    wl = load_watchlist()
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    show(f'<div class="tx updated">{now:%H:%M:%S} 기준, {WATCH_REFRESH}초마다 갱신</div>')

    sub_kr, sub_us = st.tabs(["국내", "미국"])
    with sub_kr:
        kr = wl["kr"]
        codes = tuple(i["code"] for i in kr)
        try:
            quotes = naver_stocks(codes)
        except Exception:
            quotes = {}
        prog = get_program_daily(codes[:PROGRAM_MAX_STOCKS], today) if kiwoom_keys() and codes else {}
        if kr:
            cards = "".join(watch_card(i, quotes.get(i["code"]), prog.get(i["code"], {}).get("rows", []), today)
                            for i in kr)
            show(f'<div class="tx card"><div class="card-title">국내 관심종목</div>{cards}</div>')
        else:
            show('<div class="tx card"><div class="empty">아래 편집에서 관심종목을 추가해 보세요.</div></div>')
        with st.expander("문제 해결용 정보"):
            errors = [f'{i["name"]}: {prog[i["code"]]["error"]}' for i in kr if prog.get(i["code"], {}).get("error")]
            if errors:
                st.write(errors)
            first = next((prog[c] for c in codes if prog.get(c, {}).get("rows")), None)
            if first:
                st.write("프로그램 매매 항목 이름:", list(first["rows"][0].keys()))
                st.write("첫 줄:", first["rows"][0])
            if not errors and not first:
                st.write("받은 프로그램 매매 데이터가 없어요.")

        investor_by_stock_section()

    with sub_us:
        if wl["us"]:
            rows = "".join(us_row_html(it) for it in wl["us"])
            show(f'<div class="tx card list">{rows}</div>')
            show('<div class="tx inv-note">장전·장후 시세는 야후에서 제공될 때만 표시돼요.</div>')
        else:
            show('<div class="tx card"><div class="empty">아래 편집에서 관심종목을 추가해 보세요.</div></div>')


def watch_editor() -> None:
    with st.expander("✏️ 관심종목 추가·삭제"):
        if not pin_gate("watch"):
            return
        wl = load_watchlist()

        st.caption("종목명을 띄어쓰기로 구분해서 한 번에 넣을 수 있어요. 국내·미국을 섞어도 돼요.")
        bulk = st.text_area("여러 종목 한 번에 추가", placeholder="삼성전자 SK하이닉스 삼성전기 엔비디아",
                            key="wl_bulk", height=70)
        if st.button("추가하기", key="wl_bulk_add") and bulk.strip():
            added, missed = bulk_add_watchlist(bulk)
            if added:
                st.success("추가함: " + ", ".join(added))
            if missed:
                st.warning("못 찾음: " + ", ".join(missed))
            st.rerun()

        st.divider()
        st.caption("검색 결과가 여러 개일 때 직접 골라서 추가할 수도 있어요.")
        q = st.text_input("국내 종목 검색", placeholder="종목명 또는 코드", key="watch_q")
        if q.strip():
            try:
                results = naver_search(q.strip())
            except Exception:
                results = []
            if results:
                idx = st.selectbox("검색 결과", range(len(results)), key="watch_pick",
                                   format_func=lambda i: f'{results[i]["name"]} ({results[i]["code"]})')
                if st.button("관심종목에 추가", key="watch_add"):
                    item = results[idx]
                    if all(x["code"] != item["code"] for x in wl["kr"]):
                        wl["kr"].append(item)
                        write_json(WATCHLIST_FILE, wl)
                    st.rerun()
            else:
                st.caption("검색 결과가 없어요.")

        st.divider()
        st.caption("등록된 종목 — ✕를 누르면 바로 삭제돼요.")
        for x in list(wl["kr"]):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f'<div class="wl-item"><span class="n">🇰🇷 {html.escape(x["name"])} ({x["code"]})</span></div>',
                       unsafe_allow_html=True)
            if c2.button("✕", key=f'del_kr_{x["code"]}'):
                wl["kr"] = [y for y in wl["kr"] if y["code"] != x["code"]]
                write_json(WATCHLIST_FILE, wl)
                st.rerun()
        for x in list(wl["us"]):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f'<div class="wl-item"><span class="n">🇺🇸 {html.escape(x["name"])} ({x["ticker"]})</span></div>',
                       unsafe_allow_html=True)
            if c2.button("✕", key=f'del_us_{x["ticker"]}'):
                wl["us"] = [y for y in wl["us"] if y["ticker"] != x["ticker"]]
                write_json(WATCHLIST_FILE, wl)
                st.rerun()
        if not wl["kr"] and not wl["us"]:
            st.caption("등록된 종목이 없어요.")

# ---------------------------------------------------------------------
# 탭 5: 일정
# ---------------------------------------------------------------------
def schedule_section() -> None:
    data = load_schedule()
    level = st.segmented_control("중요도", ["전체", "★★ 이상", "★★★만"], default="★★ 이상",
                                 key="sched_level", label_visibility="collapsed") or "전체"
    min_imp = {"전체": 1, "★★ 이상": 2, "★★★만": 3}[level]
    if data.get("week"):
        show(f'<div class="tx updated">{html.escape(str(data["week"]))}</div>')
    events = [e for e in data["events"] if int(e.get("importance", 1)) >= min_imp and e.get("date")]
    if not events:
        show('<div class="tx card"><div class="empty">표시할 일정이 없어요.</div></div>')
    events.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    today = datetime.now(KST).date().isoformat()
    for d, group in groupby(events, key=lambda e: e["date"]):
        try:
            dt = date.fromisoformat(d)
            label = f"{dt.month}월 {dt.day}일 {WEEKDAYS[dt.weekday()]}"
        except ValueError:
            label = d
        badge = '<span class="today">오늘</span>' if d == today else ""
        rows = []
        for e in group:
            imp = int(e.get("importance", 1))
            dots = "".join(f'<i class="dot{" on" if k < imp else ""}"></i>' for k in range(3))
            nums = "".join(f"<span><b>{lab}</b>{html.escape(str(e.get(key, '')))}</span>"
                           for lab, key in (("실제", "actual"), ("예상", "forecast"), ("이전", "previous"))
                           if str(e.get(key, "")).strip())
            country = COUNTRIES.get(e.get("country", ""), e.get("country", ""))
            nums_html = '<div class="ev-nums">' + nums + '</div>' if nums else ""
            rows.append(f'<div class="ev"><div class="ev-time">{html.escape(e.get("time", ""))}</div>'
                        f'<div class="ev-main"><div class="ev-title"><span class="ctry">{html.escape(country)}</span>'
                        f'{html.escape(e.get("event", ""))}</div>{nums_html}</div>'
                        f'<div class="ev-imp">{dots}</div></div>')
        show(f'<div class="tx"><div class="day-head">{label}{badge}</div>'
             f'<div class="card ev-card">{"".join(rows)}</div></div>')


def schedule_editor() -> None:
    with st.expander("일정 편집"):
        if not pin_gate("sched"):
            return
        data = load_schedule()
        with st.form("add_event", clear_on_submit=True):
            c1, c2 = st.columns(2)
            d = c1.date_input("날짜", value=datetime.now(KST).date())
            t = c2.text_input("시간", placeholder="21:30")
            c3, c4 = st.columns(2)
            country = c3.selectbox("국가", list(COUNTRIES), format_func=lambda k: COUNTRIES[k])
            imp = c4.selectbox("중요도", [3, 2, 1], format_func=lambda x: "★" * x)
            name = st.text_input("일정 이름", placeholder="예: 미국 CPI")
            c5, c6 = st.columns(2)
            forecast = c5.text_input("예상", placeholder="선택")
            previous = c6.text_input("이전", placeholder="선택")
            if st.form_submit_button("일정 추가") and name.strip():
                data["events"].append({"date": d.isoformat(), "time": t.strip(), "country": country,
                                       "importance": imp, "event": name.strip(), "actual": "",
                                       "forecast": forecast.strip(), "previous": previous.strip()})
                write_json(USER_SCHEDULE_FILE, data)
                st.rerun()

        labels = [f'{e.get("date")} {e.get("time", "")} {e.get("event", "")}' for e in data["events"]]
        remove = st.multiselect("삭제할 일정", labels, key="sched_remove")
        if st.button("선택한 일정 삭제", key="sched_del") and remove:
            data["events"] = [e for e, lab in zip(data["events"], labels) if lab not in remove]
            write_json(USER_SCHEDULE_FILE, data)
            st.rerun()

        st.caption("한 주치를 한 번에 넣으려면, Claude가 만들어 준 일정 내용을 아래에 붙여넣으세요.")
        pasted = st.text_area("일정 붙여넣기", height=120, key="sched_paste",
                              placeholder='{"week": "...", "events": [...]}')
        if st.button("붙여넣은 일정으로 교체", key="sched_replace") and pasted.strip():
            try:
                new = json.loads(pasted)
                if isinstance(new, list):
                    new = {"week": "", "events": new}
                if not isinstance(new, dict) or not isinstance(new.get("events"), list):
                    raise ValueError
                write_json(USER_SCHEDULE_FILE, new)
                st.rerun()
            except Exception:
                st.error("형식이 맞지 않아요. Claude에게 받은 내용을 그대로 붙여넣어 주세요.")


# ---------------------------------------------------------------------
# 탭: 메모
# ---------------------------------------------------------------------
def load_notes() -> dict:
    data = read_json(NOTES_FILE)
    return data if isinstance(data, dict) else {}


def notes_section() -> None:
    if not pin_gate("notes"):
        return
    notes = load_notes()
    today = datetime.now(KST).date().isoformat()
    d = st.date_input("날짜", value=date.fromisoformat(today), key="notes_date")
    key = d.isoformat()
    text = st.text_area("메모", value=notes.get(key, ""), height=160, key=f"notes_text_{key}",
                        placeholder="오늘 증시에서 눈여겨본 것, 매매 이유, 다짐 등을 적어 두세요.")
    if st.button("저장", key="notes_save"):
        notes[key] = text
        write_json(NOTES_FILE, notes)
        st.success("저장했어요.")

    past = {k: v for k, v in sorted(notes.items(), reverse=True) if k != key and v.strip()}
    if past:
        show('<div class="tx section-title">지난 메모</div>')
        with st.container(border=True):
            for k, v in list(past.items())[:14]:
                try:
                    dt = date.fromisoformat(k)
                    label = f"{dt.month}월 {dt.day}일 {WEEKDAYS[dt.weekday()]}"
                except ValueError:
                    label = k
                with st.expander(label):
                    st.write(v)


# ---------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------
now = datetime.now(KST)
show(f'<div class="tx hero"><div class="hero-date">{now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]}</div>'
     f'<div class="hero-title">오늘의 시장</div></div>')

tab_index, tab_tv, tab_limit, tab_view, tab_watch, tab_sched, tab_notes = st.tabs(
    ["지수", "거래대금", "상하한가", "순위", "관심", "일정", "메모"])
with tab_index:
    show('<div class="tx section-title" style="padding-top:4px">국내 증시</div>')
    with st.container(border=True):
        index_header("KOSPI")
        index_candle("KOSPI")
        index_investors("KOSPI")
    with st.container(border=True):
        index_header("KOSDAQ")
        index_candle("KOSDAQ")
        index_investors("KOSDAQ")
    investor_chart_section()
    daily_investor_section()
    global_section()
with tab_tv:
    trade_value_section()
with tab_limit:
    limit_section()
with tab_view:
    view_rank_section()
with tab_watch:
    watch_editor()
    watch_section()
with tab_sched:
    schedule_section()
    schedule_editor()
with tab_notes:
    notes_section()

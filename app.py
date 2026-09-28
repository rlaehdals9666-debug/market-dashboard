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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo

import altair as alt
import feedparser
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
# 야후 쪽 요청에 네이버용 Referer를 보내면 차단/빈 응답이 올 수 있어서 따로 둬요.
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.yahoo.com/",
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

/* 뉴스 */
.news{display:block;padding:14px 0;border-top:1px solid var(--line);text-decoration:none !important;color:inherit !important;}
.card-title + .news,.news:first-child{border-top:none;}
.news-title{font-size:15px;font-weight:600;line-height:1.45;color:var(--text);}
.news:active .news-title{color:var(--blue);}
.news-meta{display:flex;gap:8px;font-size:12px;color:var(--sub);margin-top:6px;}
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
if not st.session_state.get("_icon_done"):
    components.html(_ICON_JS, height=0)
    st.session_state["_icon_done"] = True


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


def get_json(url: str, params: dict | None = None, timeout: int = 6, headers: dict | None = None):
    r = requests.get(url, params=params, headers=headers or HTTP_HEADERS, timeout=timeout)
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


def _naver_index_raw(code: str) -> dict:
    """캐시 없이 지수를 조회해요 (백그라운드 수집기에서도 쓰려고 분리했어요)."""
    data = get_json(f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}")
    d = (data.get("datas") or [{}])[0]
    change, rate = _apply_direction(d, to_num(d.get("compareToPreviousClosePrice")), to_num(d.get("fluctuationsRatio")))
    return {"price": to_num(d.get("closePrice")), "change": change, "rate": rate,
            "time": d.get("localTradedAt", ""), "status": d.get("marketStatus", "")}


@st.cache_data(ttl=5, show_spinner=False)
def naver_index(code: str) -> dict:
    return _naver_index_raw(code)


# ---------------------------------------------------------------------
# 공유 저장소: 서버 전체가 함께 쓰는 값이에요 (접속한 사람이 바뀌어도, 새로고침해도 유지돼요)
#  - 코스피·코스닥 시세를 10초마다 모아서 1분·3분봉을 그려요 (09:00~15:30 하루치)
#  - 최근에 실패한 주소를 잠시 건너뛰어서, 안 되는 주소 때문에 느려지지 않게 해요
# ---------------------------------------------------------------------
TICK_FILE = DATA_DIR / "ticks.json"


def _save_ticks(hub: dict) -> None:
    with hub["lock"]:
        payload = {"date": hub["date"], "ticks": {k: [list(t) for t in v] for k, v in hub["ticks"].items()}}
    try:
        write_json(TICK_FILE, payload)
    except Exception:
        pass


def _collector_loop(hub: dict) -> None:
    last_save = 0.0
    while True:
        try:
            now = datetime.now(KST)
            minutes = now.hour * 60 + now.minute
            if now.weekday() < 5 and 8 * 60 + 59 <= minutes <= 15 * 60 + 40:
                today = now.strftime("%Y%m%d")
                for code in ("KOSPI", "KOSDAQ"):
                    price = _naver_index_raw(code).get("price")
                    if price:
                        with hub["lock"]:
                            if hub["date"] != today:
                                hub["date"], hub["ticks"] = today, {}
                            hub["ticks"].setdefault(code, []).append((now.timestamp(), float(price)))
                if time.time() - last_save > 60:
                    _save_ticks(hub)
                    last_save = time.time()
                time.sleep(10)
            else:
                time.sleep(30)
        except Exception:
            time.sleep(15)


@st.cache_resource
def get_hub() -> dict:
    hub = {"lock": threading.Lock(), "ticks": {}, "date": "", "fail": {}}
    saved = read_json(TICK_FILE)
    if isinstance(saved, dict) and isinstance(saved.get("ticks"), dict):
        hub["date"] = str(saved.get("date", ""))
        hub["ticks"] = {k: [(float(a), float(b)) for a, b in v] for k, v in saved["ticks"].items()}
    threading.Thread(target=_collector_loop, args=(hub,), daemon=True).start()
    return hub


def _skip_failed(key: str) -> bool:
    return get_hub()["fail"].get(key, 0) > time.time()


def _mark_failed(key: str, minutes: int = 10) -> None:
    get_hub()["fail"][key] = time.time() + minutes * 60


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
        if _skip_failed(url):
            errors.append(f"{url} → (최근 실패해서 건너뜀)")
            continue
        try:
            return {"data": get_json(url), "url": url, "errors": errors}
        except Exception as e:
            _mark_failed(url)
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
        fkey = f"{url}|{params.get('marketType', '')}"
        if _skip_failed(fkey):
            errors.append(f"{url} {params} → (최근 실패해서 건너뜀)")
            continue
        try:
            data = get_json(url, params)
            content = data.get("content") if isinstance(data, dict) else None
            if content:
                return {"rows": content, "url": url, "params": params, "errors": errors}
            errors.append(f"{url} {params} → 빈 응답")
        except Exception as e:
            _mark_failed(fkey)
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
        fkey = f"{url}|{params.get('marketType', '')}"
        if _skip_failed(fkey):
            errors.append(f"{url} {params} → (최근 실패해서 건너뜀)")
            continue
        try:
            data = get_json(url, params)
            content = data.get("content") if isinstance(data, dict) else None
            if content:
                return {"rows": content, "url": url, "params": params, "errors": errors}
            errors.append(f"{url} {params} → 빈 응답")
        except Exception as e:
            _mark_failed(fkey)
            errors.append(f"{url} {params} → {e}")
    return {"rows": [], "url": None, "params": None, "errors": errors}


INSTITUTION_CODES = {"1000", "2000", "3000", "3100", "4000", "5000", "6000"}


_INV_WORDS = {
    "개인": ("personal", "individual", "indiv", "retail", "개인"),
    "외국인": ("foreign", "frgn", "외국"),
    "기관": ("institution", "instit", "organ", "orgn", "기관"),
}


def _who(text) -> str | None:
    t = str(text).lower()
    for who, words in _INV_WORDS.items():
        if any(w in t for w in words):
            return who
    return None


def _code_who(v) -> str | None:
    sv = str(v)
    if sv == "8000":
        return "개인"
    if sv in ("9000", "9001"):
        return "외국인"
    if sv in INSTITUTION_CODES:
        return "기관"
    return None


def _num_or_none(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and re.fullmatch(r"[+-]?[\d,]+(\.\d+)?", v.strip()):
        return to_num(v)
    return None


def _amount_of(item: dict):
    best = None
    for k, v in item.items():
        val = _num_or_none(v)
        lk = str(k).lower()
        if val is None or any(w in lk for w in ("code", "gubun", "type", "rank", "seq")) or lk in ("id", "no"):
            continue
        prio = 2 if "net" in lk else (1 if ("amount" in lk or "value" in lk) else 0)
        if best is None or prio > best[1]:
            best = (val, prio)
    return best[0] if best else None


def extract_investors(row) -> dict | None:
    """응답 한 줄에서 개인/외국인/기관 순매수를 찾아요. 필드 이름을 확실히 몰라서 여러 형태를 시도해요."""
    if not isinstance(row, dict):
        return None
    found: dict = {}

    def put(who, val, prio):
        if who and val is not None and (who not in found or prio > found[who][1]):
            found[who] = (val, prio)

    for k, v in row.items():                      # 1) 키 이름에 투자자 이름이 들어 있는 경우
        who, val = _who(k), _num_or_none(v)
        if who and val is not None:
            lk = str(k).lower()
            put(who, val, 2 if "net" in lk else (0 if any(w in lk for w in ("buy", "sell", "bid", "ask")) else 1))

    for k, v in row.items():                      # 2) 하위 dict/list 안에 있는 경우
        if isinstance(v, dict):
            for kk, vv in v.items():
                put(_who(kk), _num_or_none(vv), 2)
        items = v if isinstance(v, list) else []
        sums: dict = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            who = None
            for vv in it.values():
                if isinstance(vv, (str, int)) and not isinstance(vv, bool):
                    who = _code_who(vv) or (_who(vv) if isinstance(vv, str) else None)
                    if who:
                        break
            amt = _amount_of(it)
            if who and amt is not None:
                sums[who] = sums.get(who, 0.0) + amt
        for who, val in sums.items():
            put(who, val, 1.5)
    return {who: v[0] for who, v in found.items()} if found else None


def parse_trend_row(row: dict) -> dict:
    inv = extract_investors(row) or {}
    out = {who: inv.get(who) for who in ("개인", "외국인", "기관")}
    out["time"] = str(pick(row, "time", "localTradedAt", "bizdate", "date")) if isinstance(row, dict) else ""
    return out


def has_investor_data(p: dict | None) -> bool:
    return bool(p) and any(p.get(k) is not None for k in ("개인", "외국인", "기관"))


def latest_trend_row(rows: list) -> dict | None:
    """시간이 가장 늦은 줄을 골라요 (응답 순서가 어느 쪽인지 몰라서 시간으로 판단해요)."""
    parsed = [parse_trend_row(r) for r in rows]
    parsed = [p for p in parsed if has_investor_data(p)]
    if not parsed:
        return None
    return max(parsed, key=lambda p: re.sub(r"\D", "", p["time"]))


def hhmm_of(text: str) -> str | None:
    digits = re.sub(r"\D", "", str(text))
    if len(digits) >= 12:
        return digits[8:12]
    if len(digits) in (4, 6):
        return digits[:4]
    return None


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
# 주말주가: Hyperliquid에 상장된 삼성전자·SK하이닉스 24시간 토큰화 선물
# (실험적 기능이에요 — Hyperliquid의 커스텀 덱스 구조상 정확한 코인 심볼을 확인하지 못했어요)
# ---------------------------------------------------------------------
HYPERLIQUID_SYMBOLS = {"삼성전자": {"symbol": "SMSN", "code": "005930"}, "SK하이닉스": {"symbol": "SKHX", "code": "000660"}}

# 미국 주식 한글 표시 이름: 야후 검색은 영어 이름만 줘서, 자주 쓰는 종목은 한글로 바꿔 보여줘요.
# (이 목록에 없는 티커는 야후에서 받은 영어 이름 그대로 표시돼요)
KOREAN_US_NAMES = {
    "RKLB": "로켓 랩", "LMT": "록히드 마틴", "NOC": "노스롭 그루만", "RTX": "알티엑스",
    "LLY": "일라이 릴리", "MRNA": "모더나", "NVO": "노보노디스크", "PFE": "화이자", "JNJ": "존슨 앤드 존슨",
    "UUUU": "에너지 퓨얼스", "USAR": "USA 레어 어스", "MP": "MP 머티리얼스",
    "BE": "블룸 에너지", "ENPH": "엔페이즈 에너지", "FSLR": "퍼스트 솔라", "SLB": "슬럼버제이",
    "POWL": "파워 인더스트리스", "VRT": "버티브 홀딩스", "GEV": "GE 버노바", "XOM": "엑슨 모빌", "CVX": "셰브론",
    "TSLA": "테슬라", "ALB": "알버말", "RIVN": "리비안 오토모티브", "LAC": "리튬 아메리카스",
    "MU": "마이크론 테크놀로지", "INTC": "인텔", "SNDK": "샌디스크", "NVDA": "엔비디아", "AVGO": "브로드컴",
    "AMD": "AMD", "TSM": "TSMC", "MRVL": "마벨 테크놀로지", "DELL": "델 테크놀로지스",
    "STX": "시게이트 테크놀로지", "PLTR": "팔란티어 테크", "GOOGL": "알파벳 A", "META": "메타 플랫폼스",
    "MSFT": "마이크로소프트", "CBRS": "세레브라스 시스템즈", "CRWV": "코어위브", "ORCL": "오라클",
    "COHR": "코히어런트", "QCOM": "퀄컴", "GLW": "코닝",
    "MSTR": "스트래티지", "CRCL": "써클 인터넷 그룹", "GEMI": "제미니 스페이스 스테이션", "COIN": "코인베이스 글로벌",
    "QUBT": "퀀텀 컴퓨팅", "IONQ": "아이온큐", "RGTI": "리게티 컴퓨팅", "ZS": "지스케일러",
    "CRWD": "크라우드스트라이크", "PANW": "팔로 앨토 네트웍스", "CIBR": "사이버보안 ETF",
    "SMR": "뉴스케일 파워", "OKLO": "오클로", "NNE": "나노 뉴클리어 에너지",
    "BABA": "알리바바 그룹", "COST": "코스트코 홀세일", "AMZN": "아마존닷컴", "RBLX": "로블록스",
    "AAPL": "애플", "NFLX": "넷플릭스", "V": "비자", "GM": "제너럴 모터스", "BA": "보잉",
    "TEM": "템퍼스 AI", "ARM": "에이알엠 홀딩스", "CRM": "세일즈포스",
    "BAC": "뱅크오브아메리카", "JPM": "제이피모간 체이스", "GS": "골드만삭스", "C": "씨티그룹",
    "CSCO": "시스코 시스템즈", "DAL": "델타 항공", "EXPE": "익스피디아 그룹",
    "SKHY": "SK하이닉스(ADR)", "SPCX": "스페이스X",
}


@st.cache_data(ttl=15, show_spinner=False)
def hyperliquid_mids() -> dict:
    """Hyperliquid의 allMids로 모든 심볼의 중간가를 한 번에 받아와요.
    삼성전자·SK하이닉스 같은 토큰화 주식 선물은 'xyz'라는 별도 dex(HIP-3)에 있어요."""
    out = {"mids": {}, "error": None, "dex_tried": []}
    for dex in ("xyz", ""):
        try:
            r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "allMids", "dex": dex}, timeout=6)
            r.raise_for_status()
            data = r.json()
            out["dex_tried"].append(dex or "(기본)")
            if isinstance(data, dict) and data:
                out["mids"].update(data)
        except Exception as e:
            out["error"] = str(e)
    return out


def weekend_price_card(name: str, symbol: str, mid_usd: float | None, usdkrw: float | None,
                        krx_close: float | None) -> str:
    if not mid_usd:
        return (f'<div class="tx card"><div class="ix-name">{html.escape(name)}</div>'
                f'<div class="empty">지금은 가격을 못 찾았어요 (심볼: {html.escape(symbol)})</div></div>')
    krw = mid_usd * usdkrw if usdkrw else None
    if krw is None:
        return (f'<div class="tx card"><div class="ix-name">{html.escape(name)}</div>'
                f'<div class="ix-val flat">&#36;{mid_usd:,.2f}</div>'
                f'<div class="empty">원/달러 환율을 못 불러와서 원화 환산을 못 했어요.</div></div>')

    gap_html = ""
    if krx_close:
        gap_rate = (krw - krx_close) / krx_close * 100
        cls = sign_cls(gap_rate)
        gap_html = f'<div class="chg {cls}">{signed(gap_rate)}% <span class="row-sub">한국 종가 대비</span></div>'
        cls_val = cls
    else:
        cls_val = "flat"

    krx_html = f' · 한국 종가 {krx_close:,.0f}원' if krx_close else ""
    return (f'<div class="tx card"><div class="ix-name">{html.escape(name)} <span class="row-sub">'
            f'({html.escape(symbol)}-USD, Hyperliquid)</span></div>'
            f'<div class="ix-val {cls_val}">{krw:,.0f}원</div>{gap_html}'
            f'<div class="row-sub" style="margin-top:6px">&#36;{mid_usd:,.2f}{krx_html}</div></div>')


@st.fragment(run_every=15)
def weekend_price_section() -> None:
    now = datetime.now(KST)
    show(f'<div class="tx updated">{now:%H:%M:%S} 기준, 15초마다 갱신돼요. '
         f'Hyperliquid라는 해외 코인 거래소에 상장된 24시간 토큰화 선물이에요. '
         f'실제 삼성전자·SK하이닉스 주가와는 다를 수 있고, 거래량이 적으면 가격이 튈 수 있어요.</div>')
    result = hyperliquid_mids()
    usdkrw_q = yf_quote("KRW=X")
    usdkrw = usdkrw_q["price"] if usdkrw_q else None
    try:
        krx_quotes = naver_stocks(tuple(v["code"] for v in HYPERLIQUID_SYMBOLS.values()))
    except Exception:
        krx_quotes = {}
    for name, info in HYPERLIQUID_SYMBOLS.items():
        price = result["mids"].get(info["symbol"]) or result["mids"].get(f'xyz:{info["symbol"]}')
        krx_close = krx_quotes.get(info["code"], {}).get("price")
        show(weekend_price_card(name, info["symbol"], float(price) if price else None, usdkrw, krx_close))
        st.write("")
    with st.expander("문제 해결용 정보"):
        st.write("시도한 dex:", result["dex_tried"])
        st.write("오류:", result["error"])
        sample = {k: v for k, v in list(result["mids"].items())[:15]}
        st.write("받아온 심볼 예시(최대 15개):", sample)
        st.caption("찾는 심볼(SMSN, SKHX)이 위 목록에 없다면 알려주세요 — 실제 심볼 이름을 다시 확인해 볼게요.")


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
INDEX_INFO = {  # 코드: (표시 이름, 야후 심볼, kr=한국·us=미국)
    "KOSPI": ("코스피", "^KS11", "kr"),
    "KOSDAQ": ("코스닥", "^KQ11", "kr"),
    "SPX": ("S&P 500", "^GSPC", "us"),
    "IXIC": ("나스닥", "^IXIC", "us"),
    "NDX": ("US Tech 100 (나스닥100)", "^NDX", "us"),
}
YF_SYMBOLS = ("KRW=X", "^VIX", "CL=F", "BZ=F", "^GSPC", "^IXIC", "^NDX")   # 한 번에 동시에 조회해요


def eok_or_dash(v) -> str:
    return "-" if v is None else eok(v)


def inv_line(label: str, values: dict | None) -> str:
    if not values or all(values.get(k) is None for k in ("외국인", "기관", "개인")):
        return f'<div class="inv-line"><span class="lbl">{label}</span><span>데이터를 읽지 못했어요</span></div>'
    cells = "".join(f'<span>{who}<b class="{sign_cls(values.get(who) or 0)}">{eok_or_dash(values.get(who))}</b></span>'
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
# 분봉(1분·3분) 차트: 하루 세션 전체를 고정된 시간 축으로 보여줘요
#  - 코스피·코스닥: 서버가 10초마다 모아 둔 시세(get_hub)로 그려요 (09:00~15:30)
#  - 미국 지수: 야후의 1분 데이터로 그려요 (미국 정규장 시간을 한국시간으로 바꿔서 표시)
# ---------------------------------------------------------------------
def get_tick_df(code: str):
    hub = get_hub()
    with hub["lock"]:
        rows = list(hub["ticks"].get(code, []))
        day = hub["date"]
    if not rows:
        return pd.DataFrame(columns=["date", "price"]), day
    df = pd.DataFrame(rows, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(KST).dt.tz_localize(None)
    return df[["date", "price"]], day


def kr_window(day: str):
    if day:
        d = datetime.strptime(day, "%Y%m%d")
    else:
        d = datetime.now(KST).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    return d.replace(hour=9, minute=0), d.replace(hour=15, minute=30)


@st.cache_data(ttl=30, show_spinner=False)
def us_intraday(symbol: str):
    """미국 지수의 1분 데이터 (open/high/low/close)와, 정규장 시간(한국시간)을 돌려줘요."""
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    try:
        h = yf.Ticker(symbol).history(period="1d", interval="1m")
        if h.empty:
            return empty, None
        idx = h.index.tz_localize("America/New_York") if h.index.tz is None else h.index.tz_convert("America/New_York")
        day = idx[0].date()
        et = ZoneInfo("America/New_York")
        start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=et).astimezone(KST).replace(tzinfo=None)
        end = datetime(day.year, day.month, day.day, 16, 0, tzinfo=et).astimezone(KST).replace(tzinfo=None)
        df = pd.DataFrame({"date": idx.tz_convert(KST).tz_localize(None), "open": h["Open"].values,
                           "high": h["High"].values, "low": h["Low"].values, "close": h["Close"].values})
        return df.dropna(), (start, end)
    except Exception:
        return empty, None


def ohlc_from_ticks(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    return df.set_index("date")["price"].resample(f"{minutes}min").ohlc().dropna().reset_index() \
        .rename(columns={"index": "date"})


def ohlc_resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty or minutes == 1:
        return df
    return (df.set_index("date").resample(f"{minutes}min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index())


def _dt_domain(start, end):
    return [alt.DateTime(year=start.year, month=start.month, date=start.day, hours=start.hour, minutes=start.minute),
            alt.DateTime(year=end.year, month=end.month, date=end.day, hours=end.hour, minutes=end.minute)]


def intraday_chart(d: pd.DataFrame, window, minutes: int) -> alt.Chart | None:
    """시간 축을 세션 전체(예: 09:00~15:30)로 고정해서, 시간이 지나도 앞부분이 사라지지 않아요."""
    if d is None or d.empty or window is None:
        return None
    start, end = window
    d = d[(d["date"] >= start) & (d["date"] <= end)]
    if d.empty:
        return None
    slots = max(1, int((end - start).total_seconds() / 60 / minutes))
    bar = max(1, min(12, int(280 / slots * 0.7)))
    base = alt.Chart(d).encode(
        x=alt.X("date:T", scale=alt.Scale(domain=_dt_domain(start, end)),
                axis=alt.Axis(title=None, format="%H:%M", labelColor=FLAT, grid=False, domain=False,
                              ticks=False, tickCount=6, labelFontSize=11)))
    y = alt.Y("low:Q", scale=alt.Scale(zero=False), axis=alt.Axis(title=None, orient="right", format=",.0f",
              labelColor=FLAT, gridColor="#F2F4F6", domain=False, ticks=False, tickCount=4, labelFontSize=11))
    cond = alt.condition("datum.close >= datum.open", alt.value(UP), alt.value(DOWN))
    wick = base.mark_rule(strokeWidth=1, clip=True).encode(y=y, y2="high:Q", color=cond,
        tooltip=[alt.Tooltip("date:T", title="시간", format="%H:%M"), alt.Tooltip("open:Q", title="시가", format=",.2f"),
                 alt.Tooltip("high:Q", title="고가", format=",.2f"), alt.Tooltip("low:Q", title="저가", format=",.2f"),
                 alt.Tooltip("close:Q", title="종가", format=",.2f")])
    body = base.mark_bar(size=bar, clip=True).encode(y="open:Q", y2="close:Q", color=cond)
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


def investor_chart(rows: list, end_hm=(15, 30)) -> alt.Chart | None:
    recs = []
    for r in rows:
        p = parse_trend_row(r)
        hm = hhmm_of(p["time"])
        if hm and has_investor_data(p):
            recs.append({"t": pd.Timestamp(f"1900-01-01 {hm[:2]}:{hm[2:]}"),
                         "외국인": p["외국인"], "기관": p["기관"], "개인": p["개인"]})
    if not recs:
        return None
    df = pd.DataFrame(recs).sort_values("t")
    long = df.melt(id_vars="t", value_vars=["외국인", "기관", "개인"], var_name="투자자", value_name="순매수")
    long["순매수"] = pd.to_numeric(long["순매수"], errors="coerce")
    long = long.dropna()
    if long.empty:
        return None
    domain = [alt.DateTime(year=1900, month=1, date=1, hours=9, minutes=0),
              alt.DateTime(year=1900, month=1, date=1, hours=end_hm[0], minutes=end_hm[1])]
    return (alt.Chart(long).mark_line(strokeWidth=2.2, interpolate="monotone", clip=True).encode(
        x=alt.X("t:T", scale=alt.Scale(domain=domain),
                axis=alt.Axis(title=None, format="%H:%M", labelColor=FLAT, grid=False, domain=False,
                              ticks=False, tickCount=6, labelFontSize=11)),
        y=alt.Y("순매수:Q", axis=alt.Axis(title=None, orient="right", format="~s", labelColor=FLAT,
                                        gridColor="#F2F4F6", domain=False, ticks=False, tickCount=4, labelFontSize=11)),
        color=alt.Color("투자자:N", scale=alt.Scale(domain=list(INVESTOR_COLORS), range=list(INVESTOR_COLORS.values())),
                        legend=alt.Legend(orient="top", title=None, labelFontSize=12)),
        tooltip=[alt.Tooltip("t:T", title="시간", format="%H:%M"), "투자자:N",
                 alt.Tooltip("순매수:Q", format=",.0f")],
    ).properties(height=220, width="container", background="transparent").configure_view(strokeWidth=0))


def us_header_html(name: str, q: dict | None) -> str:
    if not q:
        return f'<div class="tx"><div class="ix-name">{html.escape(name)}</div><div class="empty">지수를 불러오지 못했어요</div></div>'
    cls = sign_cls(q["change"])
    return (f'<div class="tx"><div class="ix-head"><div class="ix-name">{html.escape(name)}</div>'
            f'<div class="ix-time">약 15분 지연</div></div>'
            f'<div class="ix-row"><div class="ix-val {cls}">{q["price"]:,.2f}</div>'
            f'<div class="chg {cls}">{signed(q["change"])} ({signed(q["rate"])}%)</div></div></div>')


def _yf_quote_plain(symbol: str) -> dict | None:
    try:
        fi = yf.Ticker(symbol).fast_info
        last, prev = float(fi.last_price), float(fi.previous_close)
        change = last - prev
        return {"price": last, "change": change, "rate": change / prev * 100 if prev else 0}
    except Exception:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def yf_quotes_bulk(symbols: tuple) -> dict:
    """여러 심볼을 동시에 조회해요 (한 종목씩 차례로 조회하면 느려요)."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip(symbols, pool.map(_yf_quote_plain, symbols)))


@st.fragment(run_every=INDEX_REFRESH)
def index_header(code: str) -> None:
    name, symbol, kind = INDEX_INFO[code]
    if kind == "kr":
        try:
            q = naver_index(code)
        except Exception:
            q = None
        show(index_header_html(name, q))
    else:
        show(us_header_html(name, yf_quotes_bulk(YF_SYMBOLS).get(symbol)))


@st.fragment(run_every=20)
def index_candle(code: str) -> None:
    name, symbol, kind = INDEX_INFO[code]
    period = st.segmented_control("차트", ["1분", "3분", "일봉", "주봉", "월봉", "연봉"], default="일봉",
                                  key=f"candle_{code}", label_visibility="collapsed") or "일봉"
    if period in ("1분", "3분"):
        minutes = 1 if period == "1분" else 3
        if kind == "kr":
            ticks, day = get_tick_df(code)
            d, window = ohlc_from_ticks(ticks, minutes), kr_window(day)
            hint = "서버가 10초마다 모은 시세로 그려요. 서버가 켜져 있던 시간의 데이터만 있어요."
        else:
            raw, window = us_intraday(symbol)
            d = ohlc_resample(raw, minutes)
            hint = "야후 1분 데이터예요 (약 15분 지연). 미국 정규장 시간을 한국시간으로 보여줘요."
        chart = intraday_chart(d, window, minutes)
        if chart is None:
            show('<div class="tx empty">표시할 분봉 데이터가 아직 없어요. 장이 열린 뒤에 채워져요.</div>')
        else:
            st.altair_chart(chart, theme=None)
            show(f'<div class="tx inv-note">{hint}</div>')
        return
    try:
        df = index_history(symbol, period)
    except Exception:
        df = None
    if df is None or df.empty:
        show('<div class="tx empty">차트를 불러오지 못했어요</div>')
        return
    st.altair_chart(candle_chart(df, period), theme=None)
    show(ma_legend_html(df))


def spot_investors(code: str) -> dict | None:
    a = parse_deal_trend(naver_index_integration(code)["data"])
    if a and any(a.values()):
        return a
    row = latest_trend_row(naver_trend_time(code)["rows"])
    if row:
        return {k: row[k] for k in ("개인", "외국인", "기관")}
    return a


def futures_investors() -> dict | None:
    row = latest_trend_row(naver_trend_time("FUT")["rows"])
    return {k: row[k] for k in ("개인", "외국인", "기관")} if row else None


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
        chart = investor_chart(trend["rows"], (15, 45) if target == "선물" else (15, 30)) if trend["rows"] else None
        if chart is not None:
            st.altair_chart(chart, theme=None)
        elif trend["rows"]:
            show('<div class="tx empty">데이터 형식을 읽지 못했어요. 아래 "문제 해결용 정보"를 캡처해 보내주세요.</div>')
        else:
            show('<div class="tx empty">오늘의 시간별 데이터가 아직 없어요. 장중에 표시돼요.</div>')
        with st.expander("문제 해결용 정보"):
            st.write("주소:", trend["url"] or trend["errors"])
            if trend["rows"]:
                st.write("첫 줄:", trend["rows"][0])


@st.fragment(run_every=300)
def daily_investor_section() -> None:
    """영웅문 [0784] 일별동향과 비슷하게, 최근 며칠 시장 전체 수급을 보여줘요."""
    show('<div class="tx section-title">일별 투자자 동향</div>')
    with st.container(border=True):
        target = st.segmented_control("시장", ["코스피", "코스닥"], default="코스피", key="inv_daily",
                                      label_visibility="collapsed") or "코스피"
        daily = naver_trend_daily({"코스피": "KOSPI", "코스닥": "KOSDAQ"}[target])
        parsed = [p for p in (parse_trend_row(r) for r in daily["rows"]) if has_investor_data(p)][:10]
        if not daily["rows"]:
            show('<div class="tx empty">일별 데이터를 아직 못 받아왔어요.</div>')
        elif not parsed:
            show('<div class="tx empty">데이터 형식을 읽지 못했어요. 아래 "문제 해결용 정보"를 캡처해 보내주세요.</div>')
        else:
            lines = []
            for p in parsed:
                dt = p["time"]
                label = fmt_dt(dt) if dt and dt[:8].isdigit() else str(dt)
                cells = "".join(f'<span>{who}<b class="{sign_cls(p[who] or 0)}">{eok_or_dash(p[who])}</b></span>'
                                for who in ("외국인", "기관", "개인"))
                lines.append(f'<div class="inv-line"><span class="lbl">{label}</span>{cells}</div>')
            show(f'<div class="tx">{"".join(lines)}</div>')
        with st.expander("문제 해결용 정보"):
            st.write(daily["url"] or daily["errors"])
            if daily["rows"]:
                st.write("첫 줄:", daily["rows"][0])


@st.fragment(run_every=30)
def fx_section() -> None:
    q = yf_quotes_bulk(YF_SYMBOLS)
    usdkrw_html = mini_card("원/달러", q.get("KRW=X"))
    btc_html = mini_card("비트코인", upbit_btc(), lambda v: "{:,.0f}만원".format(v / 10000))
    show(f'<div class="tx mini-grid" style="margin-top:4px">{usdkrw_html}{btc_html}</div>')


@st.fragment(run_every=30)
def vix_oil_section() -> None:
    q = yf_quotes_bulk(YF_SYMBOLS)
    show(f'<div class="tx mini-grid" style="margin-top:8px">{mini_card("VIX", q.get("^VIX"))}</div>')
    show('<div class="tx section-title">국제 유가 (선물)</div>')
    oil_html = simple_line("WTI", q.get("CL=F"), lambda v: f"&#36;{v:,.2f}") + \
               simple_line("브렌트유", q.get("BZ=F"), lambda v: f"&#36;{v:,.2f}")
    show(f'<div class="tx card">{oil_html}<div class="inv-note">약 10분 지연</div></div>')


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
        ups = sorted([r for r in get_limit_rank("7") if r["rate"] >= LIMIT_RATE], key=lambda r: -r["rate"])
        downs = sorted([r for r in get_limit_rank("8") if r["rate"] <= -LIMIT_RATE], key=lambda r: r["rate"])
    except Exception as e:
        err = str(e)
        used_fallback = True
        try:
            rows = get_limit_movers()
            ups = sorted([r for r in rows if r["rate"] >= LIMIT_RATE], key=lambda r: -r["rate"])
            downs = sorted([r for r in rows if r["rate"] <= -LIMIT_RATE], key=lambda r: r["rate"])
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
    """미국 종목명을 티커로 바꿔요. yfinance 내장 검색을 먼저 쓰고, 안 되면 야후 검색 API로 직접 시도해요."""
    us_exchanges = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BATS", "PNK", "NAS", "NYS"}
    out = []
    try:
        results = yf.Search(query, max_results=8).quotes
        for q in results:
            if q.get("quoteType") == "EQUITY" and q.get("exchange") in us_exchanges and q.get("symbol"):
                out.append({"name": q.get("shortname") or q.get("longname") or q["symbol"], "ticker": q["symbol"]})
        if out:
            return out
    except Exception:
        pass
    try:
        data = get_json("https://query2.finance.yahoo.com/v1/finance/search",
                        {"q": query, "quotesCount": 8, "newsCount": 0}, headers=YAHOO_HEADERS)
        for q in data.get("quotes", []):
            if q.get("quoteType") == "EQUITY" and q.get("exchange") in us_exchanges and q.get("symbol"):
                out.append({"name": q.get("shortname") or q.get("longname") or q["symbol"], "ticker": q["symbol"]})
    except Exception:
        pass
    return out


def _frame(df, ticker: str):
    try:
        return df[ticker] if isinstance(df.columns, pd.MultiIndex) else df
    except Exception:
        return None


@st.cache_data(ttl=45, show_spinner=False)
def us_quotes_bulk(tickers: tuple) -> dict:
    """미국 관심종목 시세를 두 번의 요청으로 한꺼번에 받아와요 (종목마다 따로 요청하면 70개일 때 매우 느려요).
    정규장 종가·등락률과, 장전/장후 가격(정규장 종가 대비 등락률)을 계산해요."""
    out = {t: None for t in tickers}
    if not tickers:
        return out
    names = list(tickers)
    try:
        daily = yf.download(names, period="10d", interval="1d", group_by="ticker", progress=False,
                            threads=True, auto_adjust=False)
    except Exception:
        return out
    try:
        intra = yf.download(names, period="2d", interval="1m", prepost=True, group_by="ticker", progress=False,
                            threads=True, auto_adjust=False)
    except Exception:
        intra = None
    et = "America/New_York"
    for t in tickers:
        try:
            fd = _frame(daily, t)
            d = fd["Close"].dropna()
            if len(d) < 2:
                continue
            reg, prev = float(d.iloc[-1]), float(d.iloc[-2])
            base = {"price": reg, "change": reg - prev, "rate": (reg - prev) / prev * 100 if prev else 0}
            extra = None
            fi = _frame(intra, t) if intra is not None else None
            i = fi["Close"].dropna() if fi is not None else None
            if i is not None and len(i):
                ts = pd.Timestamp(i.index[-1])
                ts = ts.tz_convert(et) if ts.tzinfo else ts.tz_localize(et)
                minutes = ts.hour * 60 + ts.minute
                px = float(i.iloc[-1])
                last_day = pd.Timestamp(d.index[-1]).date()
                label = None
                if 4 * 60 <= minutes < 9 * 60 + 30 and ts.date() > last_day:
                    label = "장전"
                elif 16 * 60 <= minutes < 20 * 60 and ts.date() == last_day:
                    label = "장후"
                if label and reg:
                    extra = {"label": label, "price": px, "rate": (px - reg) / reg * 100}
            out[t] = {"regular": base, "extra": extra}
        except Exception:
            continue
    return out


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
            display = KOREAN_US_NAMES.get(item["ticker"].upper(), item["name"])
            if all(x["ticker"] != item["ticker"] for x in wl["us"]):
                wl["us"].append({"name": display, "ticker": item["ticker"]})
            added.append(f'{display}({item["ticker"]})')
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


def us_display_name(it: dict) -> str:
    return KOREAN_US_NAMES.get(it["ticker"].upper(), it["name"])


def us_row_html(it: dict, ext: dict | None) -> str:
    name = us_display_name(it)
    if not ext:
        right = '<div class="row-sub">-</div>'
    else:
        base = ext["regular"]
        rt, cls = rate_text(base["rate"])
        right = (f'<div class="row-price">{usd(base["price"])}</div>'
                 f'<div class="chg {cls}">정규장 {rt}</div>')
        if ext["extra"]:
            ert, ecls = rate_text(ext["extra"]["rate"])
            right += (f'<div class="chg {ecls}">{ext["extra"]["label"]} {usd(ext["extra"]["price"])} {ert}</div>')
    return (f'<div class="row">{avatar(name, logo_us(it["ticker"]))}'
            f'<div class="row-main"><div class="row-name">{html.escape(name)}</div>'
            f'<div class="row-sub">{html.escape(it["ticker"])}</div></div><div class="row-right">{right}</div></div>')


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

    # 탭 대신 선택 버튼을 써서, 고른 쪽(국내 또는 미국)만 조회해요. (둘 다 조회하면 느려요)
    market_pick = st.segmented_control("구분", ["국내", "미국"], default="국내", key="watch_market",
                                       label_visibility="collapsed") or "국내"
    if market_pick == "국내":
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

    else:
        if wl["us"]:
            exts = us_quotes_bulk(tuple(it["ticker"] for it in wl["us"]))
            rows = "".join(us_row_html(it, exts.get(it["ticker"])) for it in wl["us"])
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
            c1.markdown(f'<div class="wl-item"><span class="n">🇺🇸 {html.escape(us_display_name(x))} ({x["ticker"]})</span></div>',
                       unsafe_allow_html=True)
            if c2.button("✕", key=f'del_us_{x["ticker"]}'):
                wl["us"] = [y for y in wl["us"] if y["ticker"] != x["ticker"]]
                write_json(WATCHLIST_FILE, wl)
                st.rerun()
        if not wl["kr"] and not wl["us"]:
            st.caption("등록된 종목이 없어요.")


# ---------------------------------------------------------------------
# 탭: 뉴스
# ---------------------------------------------------------------------
# 네이버 뉴스 분류 번호(sid1/sid2)예요. 공식 문서가 없어서 알려진 값을 쓰고, 안 되면 비슷한 주제 뉴스로 대신해요.
NAVER_NEWS_SECTIONS = {
    "증권": ("101", "258"),          # 경제 > 증권
    "미국·중남미": ("104", "232"),    # 세계 > 미국/중남미
    "중동·아프리카": ("104", "234"),  # 세계 > 중동/아프리카
}
NEWS_FEEDS = {  # 네이버 조회가 안 될 때만 쓰는 대체 검색어
    "증권": "코스피 증권 국내 증시",
    "미국·중남미": "미국 중남미 국제 정세",
    "중동·아프리카": "중동 아프리카 국제 정세",
}
NEWS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def _strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def parse_naver_news(doc: str, limit: int) -> list:
    """네이버 뉴스 목록 페이지(HTML)에서 기사 제목·링크·언론사·시간을 뽑아요."""
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
                      "time_text": when.group(1).strip() if when else "", "published": None})
        if len(items) >= limit:
            return items
    if items:
        return items
    # 예비: 기사 링크가 걸린 모든 <a> (페이지 구조가 달라졌을 때)
    for m in re.finditer(r'<a\b[^>]*href="([^"]*(?:/article/|read\.naver)[^"]*)"[^>]*>(.*?)</a>', doc, re.S):
        title = _strip_tags(m.group(2))
        if len(title) < 10 or title in seen:
            continue
        seen.add(title)
        link = html.unescape(m.group(1))
        items.append({"title": title, "link": ("https://news.naver.com" + link) if link.startswith("/") else link,
                      "source": "네이버뉴스", "time_text": "", "published": None})
        if len(items) >= limit:
            break
    return items


@st.cache_data(ttl=120, show_spinner=False)
def get_naver_section_news(sid1: str, sid2: str, limit: int = 20) -> list:
    urls = [f"https://news.naver.com/breakingnews/section/{sid1}/{sid2}",
            f"https://news.naver.com/main/list.naver?mode=LSD&mid=sec&sid1={sid1}&sid2={sid2}"]
    for url in urls:
        if _skip_failed(url):
            continue
        try:
            r = requests.get(url, headers=NEWS_HEADERS, timeout=6)
            r.raise_for_status()
        except Exception:
            _mark_failed(url, 5)
            continue
        items = parse_naver_news(r.text, limit)
        if items:
            return items
    return []


@st.cache_data(ttl=180, show_spinner=False)
def get_news(keyword: str, limit: int = 20) -> list:
    """(대체용) 구글 뉴스에서 최근 기사를 가져와요."""
    q = url_quote(f"{keyword} when:1d")
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko")
    items = []
    for e in feed.entries[:limit]:
        source = e.get("source", {}).get("title", "")
        title = e.get("title", "")
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        published = None
        if e.get("published_parsed"):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        items.append({"title": title, "link": e.get("link", ""), "source": source, "time_text": "",
                      "published": published})
    return items


def news_time_ago(dt) -> str:
    if dt is None:
        return ""
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if minutes < 1:
        return "방금 전"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 60 * 24:
        return f"{minutes // 60}시간 전"
    local = dt.astimezone(KST)
    return f"{local.month}월 {local.day}일"


def news_rows_html(items: list) -> str:
    return "".join(
        f'<a class="news" href="{html.escape(i["link"], quote=True)}" target="_blank" rel="noopener">'
        f'<div class="news-title">{html.escape(i["title"])}</div>'
        f'<div class="news-meta"><span>{html.escape(i["source"])}</span>'
        f'<span>{html.escape(i.get("time_text") or news_time_ago(i["published"]))}</span></div></a>'
        for i in items
    )


@st.fragment(run_every=120)
def news_section() -> None:
    label = st.segmented_control("분류", list(NAVER_NEWS_SECTIONS), default="증권", key="news_pick",
                                 label_visibility="collapsed") or "증권"
    sid1, sid2 = NAVER_NEWS_SECTIONS[label]
    items = get_naver_section_news(sid1, sid2)
    fallback = not items
    if fallback:
        items = get_news(NEWS_FEEDS[label])
    if not items:
        show('<div class="tx card"><div class="empty">뉴스를 가져오지 못했어요.</div></div>')
        return
    if fallback:
        show('<div class="tx inv-note">네이버 뉴스를 못 읽어서 비슷한 주제의 기사로 대신 보여드려요.</div>')
    show(f'<div class="tx card">{news_rows_html(items)}</div>')


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

# 예전에는 st.tabs로 탭을 모두 그려서, 보지 않는 탭까지 동시에 계속 조회해 느렸어요.
# 이제 고른 메뉴 하나만 그려요.
get_hub()   # 코스피·코스닥 시세 수집기 시작 (서버가 켜져 있는 동안 계속 돌아요)
SECTIONS = ["지수", "거래대금", "상하한가", "순위", "관심", "뉴스", "주말주가", "일정", "메모"]
picked = st.pills("메뉴", SECTIONS, default="지수", key="main_section", label_visibility="collapsed")
section = picked or st.session_state.get("_last_section", "지수")   # 같은 버튼을 다시 눌러 선택이 풀려도 유지
st.session_state["_last_section"] = section

if section == "지수":
    show('<div class="tx section-title" style="padding-top:4px">국내 증시</div>')
    for code in ("KOSPI", "KOSDAQ"):
        with st.container(border=True):
            index_header(code)
            index_candle(code)
            index_investors(code)
    investor_chart_section()
    daily_investor_section()
    fx_section()
    show('<div class="tx section-title">뉴욕 증시</div>')
    for code in ("SPX", "IXIC", "NDX"):
        with st.container(border=True):
            index_header(code)
            index_candle(code)
    vix_oil_section()
elif section == "거래대금":
    trade_value_section()
elif section == "상하한가":
    limit_section()
elif section == "순위":
    view_rank_section()
elif section == "관심":
    watch_editor()
    watch_section()
elif section == "뉴스":
    news_section()
elif section == "주말주가":
    weekend_price_section()
elif section == "일정":
    schedule_section()
    schedule_editor()
elif section == "메모":
    notes_section()

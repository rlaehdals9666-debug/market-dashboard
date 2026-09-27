"""
오늘의 시장 - 개인 마켓 대시보드
- 시세: 한국/미국 주식·지수(yfinance, 약간 지연), 코인(업비트, 실시간)
- 뉴스: 구글 뉴스 RSS (최근 24시간)
- 일정: schedule.json 파일 (매주 교체)

실행: py -m streamlit run app.py
"""

import html
import json
from datetime import date, datetime, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import altair as alt
import feedparser
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# =====================================================================
# 설정: 종목이나 뉴스 키워드를 바꾸고 싶으면 여기만 수정하세요
# =====================================================================
INDICES = {
    "코스피": "^KS11",
    "코스닥": "^KQ11",
    "S&P 500": "^GSPC",
    "나스닥": "^IXIC",
    "원/달러": "KRW=X",
}
KR_STOCKS = {  # 코스피는 .KS, 코스닥은 .KQ
    "삼성전자": "005930.KS",
    "SK하이닉스": "000660.KS",
    "NAVER": "035420.KS",
}
US_STOCKS = {
    "엔비디아": "NVDA",
    "애플": "AAPL",
    "테슬라": "TSLA",
}
CRYPTO = {  # 업비트 마켓 코드
    "비트코인": "KRW-BTC",
    "이더리움": "KRW-ETH",
}
NEWS_KEYWORDS = ["코스피", "미국 증시", "비트코인", "삼성전자", "엔비디아"]

REFRESH_SECONDS = 30  # 시세 자동 갱신 주기(초)
# =====================================================================

KST = ZoneInfo("Asia/Seoul")
SCHEDULE_FILE = Path(__file__).parent / "schedule.json"
COUNTRIES = {"US": "미국", "KR": "한국", "CN": "중국", "JP": "일본", "EU": "유로존", "UK": "영국", "DE": "독일"}
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
UP, DOWN, FLAT = "#F04452", "#3182F6", "#8B95A1"
AVATAR_COLORS = ["#3182F6", "#F04452", "#00B868", "#FF8A00", "#7B61FF", "#00A9C5", "#4E5968"]
PERIODS = {"1개월": ("1mo", 30), "3개월": ("3mo", 90), "6개월": ("6mo", 180)}

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

/* 탭: 세그먼트 버튼 모양 */
.stTabs [data-baseweb="tab-list"]{gap:4px;background:#E5E8EB;padding:4px;border-radius:14px;}
.stTabs [data-baseweb="tab"]{flex:1;justify-content:center;height:38px;padding:0 8px;border-radius:10px;background:transparent;}
.stTabs [data-baseweb="tab"] p{font-size:15px;font-weight:600;color:var(--sub);}
.stTabs [aria-selected="true"]{background:#FFFFFF;box-shadow:0 1px 3px rgba(0,0,0,.08);}
.stTabs [aria-selected="true"] p{color:var(--text);}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{display:none;}
.stTabs [data-baseweb="tab-panel"]{padding-top:12px;}

/* 입력 요소 */
[data-baseweb="select"]>div{background:var(--bg);border:none;border-radius:12px;}
[data-baseweb="input"],[data-baseweb="base-input"]{background:#FFFFFF !important;border:none !important;border-radius:12px;}
[data-testid="stVerticalBlockBorderWrapper"]{background:#FFFFFF;border:none !important;border-radius:20px;}
button[data-testid="stBaseButton-pills"],button[data-testid="stBaseButton-segmented_control"]{background:#FFFFFF;border-color:#FFFFFF;}
button[data-testid="stBaseButton-pillsActive"],button[data-testid="stBaseButton-segmented_controlActive"]{background:var(--text) !important;border-color:var(--text) !important;}
button[data-testid="stBaseButton-pillsActive"] p,button[data-testid="stBaseButton-segmented_controlActive"] p{color:#FFFFFF !important;}

/* 공통 */
.tx{color:var(--text);letter-spacing:-0.01em;}
.hero{padding:4px 4px 14px;}
.hero-date{font-size:14px;font-weight:500;color:var(--sub);}
.hero-title{font-size:26px;font-weight:700;margin-top:2px;}
.updated{font-size:12px;color:var(--sub);padding:0 4px;}
.card{background:var(--card);border-radius:20px;padding:18px 20px 8px;}
.card-title{font-size:17px;font-weight:700;margin-bottom:4px;}
.chg{font-size:13px;font-weight:500;font-variant-numeric:tabular-nums;}
.chg.up{color:var(--up);} .chg.down{color:var(--down);} .chg.flat{color:var(--sub);}
.empty{font-size:14px;color:var(--sub);padding:8px 0 16px;}

/* 지수 카드 */
.idx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;}
.idx{background:var(--card);border-radius:16px;padding:14px 16px;}
.idx-name{font-size:13px;font-weight:500;color:var(--sub);}
.idx-val{font-size:18px;font-weight:700;margin:4px 0 2px;font-variant-numeric:tabular-nums;}

/* 종목 리스트 */
.row{display:flex;align-items:center;gap:12px;padding:12px 0;}
.avatar{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#FFFFFF;font-size:16px;font-weight:700;flex-shrink:0;}
.row-main{flex:1;min-width:0;}
.row-name{font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.row-sub{font-size:13px;color:var(--sub);margin-top:2px;}
.row-right{text-align:right;}
.row-price{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;}

/* 차트 */
.chart-name{font-size:14px;font-weight:500;color:var(--sub);}
.chart-price{font-size:26px;font-weight:700;margin:2px 0;font-variant-numeric:tabular-nums;}

/* 뉴스 */
.news{display:block;padding:14px 0;border-top:1px solid var(--line);text-decoration:none !important;}
.card-title + .news{border-top:none;}
.news-title{font-size:15px;font-weight:600;line-height:1.45;color:var(--text);}
.news:hover .news-title{color:var(--blue);}
.news-meta{display:flex;gap:8px;font-size:12px;color:var(--sub);margin-top:6px;}

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
</style>"""
st.markdown(CSS, unsafe_allow_html=True)


def show(html_str: str) -> None:
    st.markdown(html_str, unsafe_allow_html=True)


# ---------------------------------------------------------------------
# 데이터 가져오기
# ---------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def get_stock_quotes(tickers: tuple) -> dict:
    """야후 파이낸스: {티커: (현재가, 전일 종가)}"""
    result = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).fast_info
            result[t] = (info.last_price, info.previous_close)
        except Exception:
            result[t] = (None, None)
    return result


@st.cache_data(ttl=10, show_spinner=False)
def get_crypto_quotes(markets: tuple) -> dict:
    """업비트: {마켓: (현재가, 전일 종가)}"""
    try:
        r = requests.get("https://api.upbit.com/v1/ticker", params={"markets": ",".join(markets)}, timeout=5)
        r.raise_for_status()
        return {d["market"]: (d["trade_price"], d["prev_closing_price"]) for d in r.json()}
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def get_stock_history(ticker: str, period: str) -> pd.Series:
    return yf.Ticker(ticker).history(period=period)["Close"]


@st.cache_data(ttl=600, show_spinner=False)
def get_crypto_history(market: str, days: int) -> pd.Series:
    r = requests.get(
        "https://api.upbit.com/v1/candles/days", params={"market": market, "count": days}, timeout=5
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["date"] = pd.to_datetime(df["candle_date_time_kst"])
    return df.set_index("date")["trade_price"].sort_index()


@st.cache_data(ttl=300, show_spinner=False)
def get_news(keyword: str, limit: int = 15) -> list:
    """구글 뉴스: 최근 24시간 기사"""
    q = quote(f"{keyword} when:1d")
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
        items.append({"title": title, "link": e.get("link", ""), "source": source, "published": published})
    return items


def load_schedule():
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return "error"


# ---------------------------------------------------------------------
# 표시 형식
# ---------------------------------------------------------------------
def won(v: float) -> str:
    return f"{v:,.0f}원"


def usd(v: float) -> str:
    return f"&#36;{v:,.2f}"  # &#36; = $ (마크다운 수식 변환 방지)


def num(v: float) -> str:
    return f"{v:,.2f}"


def fmt_for(code: str):
    if code in US_STOCKS.values():
        return usd
    if code in KR_STOCKS.values() or code in CRYPTO.values():
        return won
    return num


def change_info(last, prev, fmt):
    if last is None or not prev:
        return None
    diff = last - prev
    pct = diff / prev * 100
    if diff > 0:
        cls, sign = "up", "+"
    elif diff < 0:
        cls, sign = "down", "-"
    else:
        cls, sign = "flat", ""
    return {
        "cls": cls,
        "diff": f"{sign}{fmt(abs(diff))}",
        "pct": f"{sign}{abs(pct):.2f}%",
        "pct_abs": f"{abs(pct):.2f}%",
    }


def time_ago(dt) -> str:
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


# ---------------------------------------------------------------------
# 화면 조각
# ---------------------------------------------------------------------
def index_grid(quotes: dict) -> str:
    cells = []
    for name, code in INDICES.items():
        last, prev = quotes.get(code, (None, None))
        c = change_info(last, prev, num)
        val = num(last) if last is not None else "-"
        chg = f'<div class="chg {c["cls"]}">{c["pct"]}</div>' if c else '<div class="chg flat">불러오는 중</div>'
        cells.append(f'<div class="idx"><div class="idx-name">{html.escape(name)}</div><div class="idx-val">{val}</div>{chg}</div>')
    return f'<div class="tx idx-grid">{"".join(cells)}</div>'


def stock_list(title: str, items: dict, quotes: dict, fmt, sub_fn) -> str:
    rows = []
    for name, code in items.items():
        last, prev = quotes.get(code, (None, None))
        color = AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]
        price = fmt(last) if last is not None else "-"
        c = change_info(last, prev, fmt)
        chg = f'<div class="chg {c["cls"]}">{c["diff"]} ({c["pct_abs"]})</div>' if c else '<div class="chg flat">-</div>'
        rows.append(
            f'<div class="row"><div class="avatar" style="background:{color}">{html.escape(name[0])}</div>'
            f'<div class="row-main"><div class="row-name">{html.escape(name)}</div><div class="row-sub">{html.escape(sub_fn(code))}</div></div>'
            f'<div class="row-right"><div class="row-price">{price}</div>{chg}</div></div>'
        )
    return f'<div class="tx card"><div class="card-title">{title}</div>{"".join(rows)}</div>'


def make_chart(series: pd.Series, color: str, value_format: str) -> alt.Chart:
    df = series.rename("price").reset_index()
    df.columns = ["date", "price"]
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_convert(None)

    lo, hi = float(df["price"].min()), float(df["price"].max())
    pad = (hi - lo) * 0.15 or hi * 0.01
    df["base"] = lo - pad
    fill = "rgba(240,68,82,0.18)" if color == UP else "rgba(49,130,246,0.18)"

    x = alt.X("date:T", axis=alt.Axis(title=None, format="%-m/%-d", labelColor=FLAT, labelFontSize=11,
                                      grid=False, domain=False, ticks=False, tickCount=4))
    y = alt.Y("price:Q", scale=alt.Scale(domain=[lo - pad, hi + pad], nice=False),
              axis=alt.Axis(title=None, orient="right", format=value_format, labelColor=FLAT, labelFontSize=11,
                            grid=True, gridColor="#F2F4F6", domain=False, ticks=False, tickCount=4))
    base = alt.Chart(df).encode(x=x, y=y)

    area = base.mark_area(
        color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=0, y2=1,
                           stops=[alt.GradientStop(color=fill, offset=0),
                                  alt.GradientStop(color="rgba(255,255,255,0)", offset=1)])
    ).encode(y2="base:Q")
    line = base.mark_line(color=color, strokeWidth=2.2, interpolate="monotone")

    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", clear="pointerout", empty=False)
    points = base.mark_circle(size=70, color=color).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                 alt.Tooltip("price:Q", title="가격", format=value_format)],
    ).add_params(hover)
    rule = alt.Chart(df).mark_rule(color="#D1D6DB").encode(x="date:T").transform_filter(hover)

    return (area + line + rule + points).properties(height=240, width="container", background="transparent") \
        .configure_view(strokeWidth=0)


@st.fragment(run_every=REFRESH_SECONDS)
def quotes_section() -> None:
    stock_quotes = get_stock_quotes(tuple([*INDICES.values(), *KR_STOCKS.values(), *US_STOCKS.values()]))
    crypto_quotes = get_crypto_quotes(tuple(CRYPTO.values()))
    now = datetime.now(KST)

    show(f'<div class="tx updated">{now:%H:%M:%S} 기준, 코인은 실시간이고 주식은 최대 20분 늦을 수 있어요</div>')
    show(index_grid(stock_quotes))
    show(stock_list("한국 주식", KR_STOCKS, stock_quotes, won, lambda c: c.split(".")[0]))
    show(stock_list("미국 주식", US_STOCKS, stock_quotes, usd, lambda c: c))
    show(stock_list("코인", CRYPTO, crypto_quotes, won, lambda c: c.split("-")[-1]))


def chart_section() -> None:
    all_assets = {**INDICES, **KR_STOCKS, **US_STOCKS, **CRYPTO}
    with st.container(border=True):
        name = st.selectbox("종목", list(all_assets.keys()), label_visibility="collapsed")
        period = st.segmented_control("기간", list(PERIODS.keys()), default="3개월",
                                      label_visibility="collapsed") or "3개월"
        code = all_assets[name]
        yf_period, days = PERIODS[period]
        fmt = fmt_for(code)

        try:
            series = get_crypto_history(code, days) if code in CRYPTO.values() else get_stock_history(code, yf_period)
        except Exception:
            series = None
        if series is None or series.empty:
            show('<div class="tx empty">차트 데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.</div>')
            return

        first, last = float(series.iloc[0]), float(series.iloc[-1])
        c = change_info(last, first, fmt)
        color = UP if last >= first else DOWN
        chg = f'<div class="chg {c["cls"]}">{period} 동안 {c["diff"]} ({c["pct"]})</div>' if c else ""
        show(f'<div class="tx"><div class="chart-name">{html.escape(name)}</div>'
             f'<div class="chart-price">{fmt(last)}</div>{chg}</div>')

        value_format = ",.0f" if fmt is won else ",.2f"
        st.altair_chart(make_chart(series, color, value_format), theme=None)


def news_section() -> None:
    choice = st.pills("키워드", NEWS_KEYWORDS, default=NEWS_KEYWORDS[0], label_visibility="collapsed")
    custom = st.text_input("검색", placeholder="다른 키워드로 검색 (예: 반도체, 금리)", label_visibility="collapsed")
    keyword = custom.strip() or choice or NEWS_KEYWORDS[0]

    items = get_news(keyword)
    title = f'<div class="card-title">{html.escape(keyword)} 뉴스</div>'
    if not items:
        show(f'<div class="tx card">{title}<div class="empty">최근 24시간 동안 올라온 뉴스가 없어요. 다른 키워드로 검색해 보세요.</div></div>')
        return
    rows = "".join(
        f'<a class="news" href="{html.escape(i["link"], quote=True)}" target="_blank" rel="noopener">'
        f'<div class="news-title">{html.escape(i["title"])}</div>'
        f'<div class="news-meta"><span>{html.escape(i["source"])}</span><span>{time_ago(i["published"])}</span></div></a>'
        for i in items
    )
    show(f'<div class="tx card">{title}{rows}</div>')


def schedule_section() -> None:
    data = load_schedule()
    if data is None:
        show('<div class="tx card"><div class="empty">schedule.json 파일이 없어요. app.py와 같은 곳에 올려 주세요.</div></div>')
        return
    if data == "error":
        show('<div class="tx card"><div class="empty">schedule.json 형식이 잘못됐어요. Claude에게 파일을 다시 만들어 달라고 해 주세요.</div></div>')
        return

    level = st.segmented_control("중요도", ["전체", "★★ 이상", "★★★만"], default="★★ 이상",
                                 label_visibility="collapsed") or "전체"
    min_imp = {"전체": 1, "★★ 이상": 2, "★★★만": 3}[level]
    show(f'<div class="tx updated">{html.escape(str(data.get("week", "")))}</div>')

    events = [e for e in data.get("events", []) if int(e.get("importance", 1)) >= min_imp]
    if not events:
        show('<div class="tx card"><div class="empty">선택한 중요도의 일정이 없어요.</div></div>')
        return
    events.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))

    today = datetime.now(KST).date().isoformat()
    for d, group in groupby(events, key=lambda e: e.get("date", "")):
        dt = date.fromisoformat(d)
        badge = '<span class="today">오늘</span>' if d == today else ""
        rows = []
        for e in group:
            imp = int(e.get("importance", 1))
            dots = "".join(f'<i class="dot{" on" if k < imp else ""}"></i>' for k in range(3))
            nums = []
            for label, key in [("실제", "actual"), ("예상", "forecast"), ("이전", "previous")]:
                v = str(e.get(key, "")).strip()
                if v:
                    nums.append(f"<span><b>{label}</b>{html.escape(v)}</span>")
            nums_html = f'<div class="ev-nums">{"".join(nums)}</div>' if nums else ""
            country = COUNTRIES.get(e.get("country", ""), e.get("country", ""))
            rows.append(
                f'<div class="ev"><div class="ev-time">{html.escape(e.get("time", ""))}</div>'
                f'<div class="ev-main"><div class="ev-title"><span class="ctry">{html.escape(country)}</span>'
                f'{html.escape(e.get("event", ""))}</div>{nums_html}</div>'
                f'<div class="ev-imp">{dots}</div></div>'
            )
        show(f'<div class="tx"><div class="day-head">{dt.month}월 {dt.day}일 {WEEKDAYS[dt.weekday()]}{badge}</div>'
             f'<div class="card ev-card">{"".join(rows)}</div></div>')


# ---------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------
now = datetime.now(KST)
show(f'<div class="tx hero"><div class="hero-date">{now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]}</div>'
     f'<div class="hero-title">오늘의 시장</div></div>')

tab_quotes, tab_news, tab_schedule = st.tabs(["시세", "뉴스", "일정"])
with tab_quotes:
    quotes_section()
    chart_section()
with tab_news:
    news_section()
with tab_schedule:
    schedule_section()

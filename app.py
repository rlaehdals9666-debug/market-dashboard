"""
내 마켓 대시보드 (1차 버전)
- 시세: 한국/미국 주식·지수(yfinance, 약간 지연), 코인(업비트, 실시간)
- 뉴스: 구글 뉴스 RSS (최근 24시간)
- 일정: schedule.json 파일 (매주 교체)

실행: streamlit run app.py
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

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

REFRESH_SECONDS = 30      # 시세 화면 자동 갱신 주기
KOREAN_COLORS = True      # True: 상승 빨강 / 하락 파랑 계열 (한국식)
# =====================================================================

KST = ZoneInfo("Asia/Seoul")
SCHEDULE_FILE = Path(__file__).parent / "schedule.json"
FLAGS = {"US": "🇺🇸", "KR": "🇰🇷", "CN": "🇨🇳", "JP": "🇯🇵", "EU": "🇪🇺", "UK": "🇬🇧", "DE": "🇩🇪"}
WEEKDAYS = "월화수목금토일"

st.set_page_config(page_title="내 마켓 대시보드", page_icon="📈", layout="wide")


# ---------------------------------------------------------------------
# 데이터 가져오기
# ---------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def get_stock_quotes(tickers: tuple) -> dict:
    """야후 파이낸스에서 현재가와 전일 대비 등락률을 가져옵니다."""
    result = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).fast_info
            last, prev = info.last_price, info.previous_close
            change = (last - prev) / prev * 100 if prev else None
            result[t] = (last, change)
        except Exception:
            result[t] = (None, None)
    return result


@st.cache_data(ttl=10, show_spinner=False)
def get_crypto_quotes(markets: tuple) -> dict:
    """업비트에서 코인 현재가와 전일 대비 등락률을 가져옵니다."""
    try:
        r = requests.get(
            "https://api.upbit.com/v1/ticker",
            params={"markets": ",".join(markets)},
            timeout=5,
        )
        r.raise_for_status()
        return {d["market"]: (d["trade_price"], d["signed_change_rate"] * 100) for d in r.json()}
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def get_stock_history(ticker: str) -> pd.Series:
    return yf.Ticker(ticker).history(period="3mo")["Close"]


@st.cache_data(ttl=600, show_spinner=False)
def get_crypto_history(market: str) -> pd.Series:
    r = requests.get(
        "https://api.upbit.com/v1/candles/days",
        params={"market": market, "count": 90},
        timeout=5,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["date"] = pd.to_datetime(df["candle_date_time_kst"])
    return df.set_index("date")["trade_price"].sort_index()


@st.cache_data(ttl=300, show_spinner=False)
def get_news(keyword: str, limit: int = 15) -> list:
    """구글 뉴스에서 최근 24시간 기사를 가져옵니다."""
    q = quote(f"{keyword} when:1d")
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko")
    items = []
    for e in feed.entries[:limit]:
        source = e.get("source", {}).get("title", "")
        title = e.get("title", "")
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        published = ""
        if e.get("published_parsed"):
            dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)
            published = dt.strftime("%m/%d %H:%M")
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
# 화면 구성
# ---------------------------------------------------------------------
def show_metrics(items: dict, quotes: dict, fmt) -> None:
    delta_color = "inverse" if KOREAN_COLORS else "normal"
    cols = st.columns(len(items))
    for col, (name, code) in zip(cols, items.items()):
        price, change = quotes.get(code, (None, None))
        if price is None:
            col.metric(name, "-", help="시세를 불러오지 못했어요. 잠시 후 다시 갱신됩니다.")
        else:
            delta = f"{change:+.2f}%" if change is not None else None
            col.metric(name, fmt(price), delta, delta_color=delta_color)


@st.fragment(run_every=REFRESH_SECONDS)
def quotes_section() -> None:
    now = datetime.now(KST)
    st.caption(f"마지막 갱신 {now:%H:%M:%S} (코인 실시간, 주식은 최대 20분 지연될 수 있어요)")

    all_tickers = tuple([*INDICES.values(), *KR_STOCKS.values(), *US_STOCKS.values()])
    stock_quotes = get_stock_quotes(all_tickers)
    crypto_quotes = get_crypto_quotes(tuple(CRYPTO.values()))

    st.subheader("주요 지수와 환율")
    show_metrics(INDICES, stock_quotes, lambda p: f"{p:,.2f}")
    st.subheader("🇰🇷 한국 주식")
    show_metrics(KR_STOCKS, stock_quotes, lambda p: f"{p:,.0f}원")
    st.subheader("🇺🇸 미국 주식")
    show_metrics(US_STOCKS, stock_quotes, lambda p: f"${p:,.2f}")
    st.subheader("₿ 코인")
    show_metrics(CRYPTO, crypto_quotes, lambda p: f"{p:,.0f}원")


def chart_section() -> None:
    all_assets = {**INDICES, **KR_STOCKS, **US_STOCKS, **CRYPTO}
    name = st.selectbox("차트로 볼 종목 (최근 3개월)", list(all_assets.keys()))
    code = all_assets[name]
    try:
        series = get_crypto_history(code) if code in CRYPTO.values() else get_stock_history(code)
        st.line_chart(series.rename(name), height=320)
    except Exception:
        st.warning("차트 데이터를 불러오지 못했어요. 인터넷 연결을 확인해 주세요.")


def news_section() -> None:
    keyword = st.radio("키워드", NEWS_KEYWORDS, horizontal=True, label_visibility="collapsed")
    custom = st.text_input("다른 키워드로 검색", placeholder="예: 반도체, 금리, 테슬라")
    keyword = custom.strip() or keyword

    items = get_news(keyword)
    if not items:
        st.info(f"'{keyword}' 관련 최근 24시간 뉴스가 없거나 불러오지 못했어요.")
        return
    for item in items:
        title = item["title"].replace("[", "［").replace("]", "］")
        st.markdown(f"**[{title}]({item['link']})**")
        st.caption(f"{item['source']}  {item['published']}")


def schedule_section() -> None:
    data = load_schedule()
    if data is None:
        st.info("schedule.json 파일이 없어요. 앱과 같은 폴더에 넣어 주세요.")
        return
    if data == "error":
        st.error("schedule.json 형식이 잘못됐어요. Claude에게 파일을 다시 만들어 달라고 해 주세요.")
        return

    st.caption(f"기간: {data.get('week', '')}  |  업데이트: {data.get('updated', '')}")
    min_imp = st.radio(
        "중요도", [1, 2, 3], index=1, horizontal=True,
        format_func=lambda x: "★" * x + " 이상",
    )

    df = pd.DataFrame(data.get("events", []))
    if df.empty:
        st.info("등록된 일정이 없어요.")
        return
    for col in ["time", "country", "event", "actual", "forecast", "previous"]:
        if col not in df:
            df[col] = ""
    df = df.fillna("")
    df = df[df["importance"] >= min_imp].sort_values(["date", "time"])
    if df.empty:
        st.info("선택한 중요도 이상의 일정이 없어요.")
        return

    today = datetime.now(KST).date().isoformat()
    for d, group in df.groupby("date", sort=True):
        dt = date.fromisoformat(d)
        label = f"{dt.month}월 {dt.day}일 ({WEEKDAYS[dt.weekday()]})"
        if d == today:
            label = "📍 오늘  " + label
        st.markdown(f"#### {label}")
        table = pd.DataFrame({
            "시간": group["time"],
            "국가": group["country"].map(lambda c: FLAGS.get(c, c)),
            "중요도": group["importance"].map(lambda x: "★" * int(x)),
            "일정": group["event"],
            "실제": group["actual"],
            "예상": group["forecast"],
            "이전": group["previous"],
        })
        st.dataframe(table, hide_index=True, width="stretch")


# ---------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------
st.title("📈 내 마켓 대시보드")
tab_quotes, tab_news, tab_schedule = st.tabs(["💹 시세", "📰 뉴스", "📅 주요 일정"])

with tab_quotes:
    quotes_section()
    st.divider()
    chart_section()

with tab_news:
    news_section()

with tab_schedule:
    schedule_section()

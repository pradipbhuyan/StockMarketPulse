"""
Indian StockPulse AI - Daily Indian Equity Picker
Streamlit app that screens Indian stock-market names using public data and OpenAI.

IMPORTANT DISCLAIMER
This app is for research, education and decision support only. It is not SEBI-registered
investment advice. Probability, target and ranking values are model estimates based on
public data and technical/fundamental heuristics. They are not guarantees of performance.
Validate all outputs before trading.

Features:
- Secure Streamlit login gate
- Streamlit secrets or environment variable credential loading
- Indian stock universe from NIFTY 50 / NIFTY Next 50 / NIFTY 100 / NIFTY 200 / custom CSV/manual list
- Public data via Yahoo Finance through yfinance using .NS symbols
- Daily technical analysis: trend, moving averages, RSI, MACD, ATR, Bollinger, volume, gap, support/resistance, relative strength
- Fundamental snapshot: market cap, PE, forward PE, PB, dividend yield, beta, ROE, margins, debt/equity where available
- Optional NSE announcements/news links placeholders and LLM synthesis
- OpenAI LLM to explain top picks and sanity-check rankings
- Top 10 Indian stocks for the day with target, stop-loss, confidence and probability estimate
- PDF and CSV export
- Manual email delivery with attachments

Required secrets or environment variables:
OPENAI_API_KEY
ADMIN_PASSWORD=choose_admin_password
PRADIP_PASSWORD=choose_pradip_password

Optional email secrets/env:
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your_sender@gmail.com
SENDER_APP_PASSWORD=your_gmail_app_password
RECIPIENT_EMAIL=recipient@example.com

Optional:
DEFAULT_MODEL=gpt-4o-mini
MAX_EMAIL_ATTACHMENT_MB=22
DEFAULT_UNIVERSE=NIFTY100
OPENAI_USE_LLM=true

Install:
pip install streamlit openai yfinance pandas numpy plotly reportlab python-dateutil

Run:
streamlit run indian_stock_pulse_ai.py
"""

from __future__ import annotations

import json
import math
import os
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from openai import OpenAI

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# -----------------------------------------------------------------------------
# App constants
# -----------------------------------------------------------------------------
APP_NAME = "Indian StockPulse AI"
APP_ICON = "📈"
APP_TAGLINE = "Daily Indian Equity Intelligence"
FILE_PREFIX = "indian_stockpulse_ai"
CREATOR_FOOTNOTE = "Research support tool created for Pradip Bhuyan. Not investment advice."
REPORT_DIR = Path("stockpulse_reports")
REPORT_DIR.mkdir(exist_ok=True)

# -----------------------------------------------------------------------------
# Streamlit setup and styling
# -----------------------------------------------------------------------------
st.set_page_config(page_title=APP_NAME, page_icon=APP_ICON, layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');
:root { --bg:#070b12; --surface:#101827; --border:#203047; --accent:#22c55e; --accent2:#06b6d4; --text:#e5eef8; --muted:#7a8ca5; --warn:#f59e0b; --bad:#ef4444; }
html, body, [data-testid="stAppViewContainer"] { background:var(--bg)!important; color:var(--text)!important; font-family:'Syne',sans-serif; }
[data-testid="stSidebar"] { background:var(--surface)!important; border-right:1px solid var(--border)!important; }
.stButton>button { background:linear-gradient(135deg,var(--accent),var(--accent2))!important; color:white!important; border:none!important; font-weight:800!important; border-radius:8px!important; }
.pulse-header{font-family:'Syne',sans-serif;font-weight:800;font-size:2.55rem;background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:0;}
.pulse-sub{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--muted);letter-spacing:0.14em;text-transform:uppercase;margin-top:4px;}
.card{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:12px;padding:1rem 1.2rem;margin-bottom:1rem;}
.warn-card{background:#211806;border:1px solid #7c4a03;border-left:4px solid var(--warn);border-radius:12px;padding:1rem 1.2rem;margin-bottom:1rem;color:#fde68a;}
.metric-box{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1rem;text-align:center;}
.metric-val{font-family:'Space Mono',monospace;font-size:1.5rem;font-weight:800;color:var(--accent);}
.metric-label{font-size:0.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.12em;}
.badge{display:inline-block;font-family:'Space Mono',monospace;font-size:0.72rem;padding:3px 9px;border-radius:999px;font-weight:700;}
.buy{background:#052e16;color:#86efac;border:1px solid #15803d}.watch{background:#2d2207;color:#fde68a;border:1px solid #a16207}.avoid{background:#2b0b0b;color:#fecaca;border:1px solid #991b1b}
.small-note{font-family:'Space Mono',monospace;font-size:0.70rem;color:var(--muted);}
</style>
""",
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
def get_config_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, None)
        if value is not None:
            return str(value)
    except Exception:
        pass
    return str(os.getenv(name, default))

OPENAI_API_KEY = get_config_value("OPENAI_API_KEY")
ADMIN_PASSWORD = get_config_value("ADMIN_PASSWORD")
PRADIP_PASSWORD = get_config_value("PRADIP_PASSWORD")
DEFAULT_MODEL = get_config_value("DEFAULT_MODEL", "gpt-4o-mini")
OPENAI_USE_LLM = get_config_value("OPENAI_USE_LLM", "true").lower() in {"1", "true", "yes", "y"}
DEFAULT_UNIVERSE = get_config_value("DEFAULT_UNIVERSE", "NIFTY100")
SMTP_HOST = get_config_value("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_config_value("SMTP_PORT", "587") or "587")
SENDER_EMAIL = get_config_value("SENDER_EMAIL")
SENDER_APP_PASSWORD = get_config_value("SENDER_APP_PASSWORD")
RECIPIENT_EMAIL = get_config_value("RECIPIENT_EMAIL")
MAX_EMAIL_ATTACHMENT_MB = float(get_config_value("MAX_EMAIL_ATTACHMENT_MB", "22"))

# -----------------------------------------------------------------------------
# Login
# -----------------------------------------------------------------------------
def require_login() -> None:
    allowed_users = {"admin": ADMIN_PASSWORD, "pradip": PRADIP_PASSWORD}
    st.session_state.setdefault("authenticated", False)
    st.session_state.setdefault("login_user", "")
    if st.session_state.authenticated:
        return

    st.markdown(f'<div class="pulse-header" style="text-align:center;margin-top:3rem;">{APP_ICON} {APP_NAME}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="pulse-sub" style="text-align:center;">Secure {APP_TAGLINE}</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("### 🔐 Sign in")
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", placeholder="admin or pradip").strip().lower()
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)
        missing = [u for u, pwd in allowed_users.items() if not pwd]
        if missing:
            st.warning("Set password keys in .streamlit/secrets.toml or env: " + ", ".join(f"{u.upper()}_PASSWORD" for u in missing))
        if submitted:
            expected = allowed_users.get(username)
            if expected and password == expected:
                st.session_state.authenticated = True
                st.session_state.login_user = username
                st.rerun()
            else:
                st.error("Invalid username or password.")
        st.caption("Allowed users: admin, pradip")
    st.stop()

require_login()

# -----------------------------------------------------------------------------
# Indian stock universes - editable starter sets
# -----------------------------------------------------------------------------
NIFTY50 = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "SBIN", "BHARTIARTL", "AXISBANK",
    "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO",
    "POWERGRID", "NTPC", "ONGC", "TATAMOTORS", "M&M", "JSWSTEEL", "TATASTEEL", "COALINDIA", "TECHM", "HCLTECH",
    "GRASIM", "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "BRITANNIA", "CIPLA", "DIVISLAB", "DRREDDY", "EICHERMOT", "HEROMOTOCO",
    "HINDALCO", "INDUSINDBK", "LTIM", "APOLLOHOSP", "BAJAJ-AUTO", "BPCL", "HDFCLIFE", "SBILIFE", "SHRIRAMFIN", "TATACONSUM",
]

NIFTY_NEXT50_EXTRA = [
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "ATGL", "BANKBARODA", "BERGEPAINT", "BOSCHLTD", "CANBK",
    "CHOLAFIN", "DABUR", "DLF", "DMART", "GAIL", "GODREJCP", "HAL", "HAVELLS", "ICICIGI", "ICICIPRULI",
    "IOC", "INDIGO", "JINDALSTEL", "LICI", "LODHA", "NAUKRI", "PIDILITIND", "PNB", "RECLTD", "MOTHERSON",
    "SIEMENS", "TATAPOWER", "TORNTPHARM", "TRENT", "TVSMOTOR", "UNIONBANK", "UNITDSPR", "VEDL", "VBL", "ZYDUSLIFE",
]

# A liquid broader list; keep manageable to avoid rate limits.
NIFTY200_EXTRA = [
    "AARTIIND", "ABCAPITAL", "ABFRL", "ALKEM", "ASHOKLEY", "ASTRAL", "AUROPHARMA", "BALKRISIND", "BANDHANBNK", "BEL",
    "BHARATFORG", "BIOCON", "BHEL", "CGPOWER", "COLPAL", "CONCOR", "CROMPTON", "CUMMINSIND", "DALBHARAT", "DEEPAKNTR",
    "FEDERALBNK", "GMRINFRA", "GNFC", "GODREJPROP", "HINDPETRO", "IDEA", "IDFCFIRSTB", "INDHOTEL", "INDUSTOWER", "IRCTC",
    "JUBLFOOD", "LALPATHLAB", "LAURUSLABS", "LICHSGFIN", "LUPIN", "MANKIND", "MARICO", "MFSL", "MPHASIS", "MRF",
    "NMDC", "OBEROIRLTY", "OFSS", "PAGEIND", "PATANJALI", "PAYTM", "PEL", "PERSISTENT", "PETRONET", "POLYCAB",
    "RAMCOCEM", "SAIL", "SBICARD", "SRF", "SUNTV", "SUPREMEIND", "TATACHEM", "TATACOMM", "TORNTPOWER", "UBL",
    "UPL", "VOLTAS", "YESBANK", "ZEEL",
]

UNIVERSES = {
    "NIFTY50": NIFTY50,
    "NIFTY100": sorted(list(dict.fromkeys(NIFTY50 + NIFTY_NEXT50_EXTRA))),
    "NIFTY200_SAMPLE": sorted(list(dict.fromkeys(NIFTY50 + NIFTY_NEXT50_EXTRA + NIFTY200_EXTRA))),
}

# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------
def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def active_report_id() -> str:
    if "report_id" not in st.session_state or not st.session_state.report_id:
        st.session_state.report_id = run_id()
    return st.session_state.report_id

def rpath(ext: str, suffix: str = "") -> Path:
    return REPORT_DIR / f"{FILE_PREFIX}_{active_report_id()}{suffix}.{ext}"

def safe_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper().replace(".NS", "")
    symbol = re.sub(r"[^A-Z0-9&\-]", "", symbol)
    return symbol

def ns_symbol(symbol: str) -> str:
    s = safe_symbol(symbol)
    return s if s.endswith(".NS") else f"{s}.NS"

def textify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {textify(x)}" for x in value)
    if isinstance(value, dict):
        return "\n".join(f"{k}: {textify(v)}" for k, v in value.items())
    return str(value)

def pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.2f}%"

def money(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"₹{float(value):,.2f}"

def save_json(data: Any, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def load_json(path: Path) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None

# -----------------------------------------------------------------------------
# Indicators
# -----------------------------------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, 12) - ema(series, 26)
    signal = ema(macd_line, 9)
    hist = macd_line - signal
    return macd_line, signal, hist

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["EMA9"] = ema(out["Close"], 9)
    out["EMA20"] = ema(out["Close"], 20)
    out["SMA50"] = out["Close"].rolling(50).mean()
    out["SMA200"] = out["Close"].rolling(200).mean()
    out["RSI14"] = rsi(out["Close"], 14)
    m, s, h = macd(out["Close"])
    out["MACD"] = m
    out["MACD_SIGNAL"] = s
    out["MACD_HIST"] = h
    out["ATR14"] = atr(out, 14)
    out["VOL20"] = out["Volume"].rolling(20).mean()
    out["RET1D"] = out["Close"].pct_change() * 100
    out["RET5D"] = out["Close"].pct_change(5) * 100
    out["RET20D"] = out["Close"].pct_change(20) * 100
    out["HIGH20"] = out["High"].rolling(20).max()
    out["LOW20"] = out["Low"].rolling(20).min()
    mid = out["Close"].rolling(20).mean()
    std = out["Close"].rolling(20).std()
    out["BB_UPPER"] = mid + 2 * std
    out["BB_LOWER"] = mid - 2 * std
    return out

# -----------------------------------------------------------------------------
# Market data
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_history(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    ticker = yf.Ticker(ns_symbol(symbol))
    hist = ticker.history(period=period, interval=interval, auto_adjust=False)
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.reset_index()
    if "Date" not in hist.columns and "Datetime" in hist.columns:
        hist = hist.rename(columns={"Datetime": "Date"})
    hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
    return hist

@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_info(symbol: str) -> dict:
    try:
        info = yf.Ticker(ns_symbol(symbol)).get_info()
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}

@st.cache_data(ttl=60 * 15, show_spinner=False)
def fetch_index_history(index_symbol: str = "^NSEI", period: str = "1y") -> pd.DataFrame:
    hist = yf.Ticker(index_symbol).history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        return pd.DataFrame()
    return hist.reset_index()

# -----------------------------------------------------------------------------
# Scoring logic
# -----------------------------------------------------------------------------
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def calculate_stock_row(symbol: str, market_ret_20d: float = 0.0) -> dict | None:
    hist = fetch_history(symbol, period="1y")
    if hist.empty or len(hist) < 220:
        return None
    data = add_indicators(hist)
    last = data.iloc[-1]
    prev = data.iloc[-2]
    info = fetch_info(symbol)

    close = float(last["Close"])
    openp = float(last["Open"])
    high = float(last["High"])
    low = float(last["Low"])
    vol = float(last.get("Volume", 0) or 0)
    atr14 = float(last.get("ATR14", np.nan))
    if not atr14 or math.isnan(atr14):
        atr14 = max(close * 0.025, 1.0)

    vol20 = float(last.get("VOL20", np.nan)) if not pd.isna(last.get("VOL20", np.nan)) else 0
    rel_vol = vol / vol20 if vol20 else 1.0
    gap_pct = ((openp - float(prev["Close"])) / float(prev["Close"])) * 100 if prev["Close"] else 0
    day_range_pct = ((high - low) / close) * 100 if close else 0
    ret20 = float(last.get("RET20D", 0) or 0)
    rel_strength = ret20 - market_ret_20d

    trend_score = 0
    if close > last["EMA9"]: trend_score += 8
    if close > last["EMA20"]: trend_score += 10
    if close > last["SMA50"]: trend_score += 10
    if close > last["SMA200"]: trend_score += 8
    if last["EMA9"] > last["EMA20"]: trend_score += 6
    if last["EMA20"] > last["SMA50"]: trend_score += 6

    momentum_score = 0
    rsi14 = float(last.get("RSI14", 50) or 50)
    if 50 <= rsi14 <= 68: momentum_score += 14
    elif 45 <= rsi14 < 50 or 68 < rsi14 <= 75: momentum_score += 8
    elif rsi14 > 75: momentum_score += 3
    if float(last.get("MACD_HIST", 0)) > 0: momentum_score += 8
    if float(last.get("RET5D", 0) or 0) > 0: momentum_score += 5
    if rel_strength > 0: momentum_score += 8

    volume_score = 0
    if rel_vol >= 2: volume_score += 14
    elif rel_vol >= 1.3: volume_score += 10
    elif rel_vol >= 0.9: volume_score += 5
    if vol > 300000: volume_score += 5

    breakout_score = 0
    high20_prev = float(data["High"].rolling(20).max().iloc[-2])
    if close >= high20_prev * 0.995: breakout_score += 12
    if close > float(prev["High"]): breakout_score += 7
    if gap_pct > 0: breakout_score += 3
    if close > openp: breakout_score += 3

    risk_penalty = 0
    if day_range_pct > 8: risk_penalty += 6
    if abs(gap_pct) > 5: risk_penalty += 5
    if rel_vol < 0.5: risk_penalty += 4

    fundamental_score = 0
    pe = info.get("trailingPE") or info.get("forwardPE")
    roe = info.get("returnOnEquity")
    beta = info.get("beta")
    debt_to_equity = info.get("debtToEquity")
    market_cap = info.get("marketCap")
    profit_margin = info.get("profitMargins")
    revenue_growth = info.get("revenueGrowth")

    if market_cap and market_cap > 500_000_000_000: fundamental_score += 6
    if pe and 5 <= float(pe) <= 60: fundamental_score += 4
    if roe and float(roe) > 0.10: fundamental_score += 5
    if profit_margin and float(profit_margin) > 0.08: fundamental_score += 4
    if revenue_growth and float(revenue_growth) > 0.03: fundamental_score += 4
    if debt_to_equity and float(debt_to_equity) < 150: fundamental_score += 3

    raw_score = trend_score + momentum_score + volume_score + breakout_score + fundamental_score - risk_penalty
    score = clamp(raw_score, 0, 100)

    target_1d = close + 0.85 * atr14
    target_pct = ((target_1d - close) / close) * 100
    stop_loss = close - 0.65 * atr14
    stop_pct = ((close - stop_loss) / close) * 100
    rr = target_pct / stop_pct if stop_pct else 0

    # Heuristic probability model. This is not calibrated to future outcomes.
    probability = clamp(42 + 0.42 * score + 2.5 * min(rel_vol, 3) + 0.4 * max(rel_strength, -5), 35, 78)
    action = "BUY / TRADE CANDIDATE" if score >= 72 and probability >= 62 else ("WATCHLIST" if score >= 58 else "AVOID / LOW PRIORITY")

    catalysts = []
    if close >= high20_prev * 0.995: catalysts.append("near/breaking 20-day high")
    if rel_vol >= 1.3: catalysts.append(f"relative volume {rel_vol:.1f}x")
    if close > last["EMA20"] and last["EMA9"] > last["EMA20"]: catalysts.append("short-term uptrend")
    if rel_strength > 0: catalysts.append("outperforming NIFTY over 20D")
    if 50 <= rsi14 <= 68: catalysts.append("healthy RSI momentum")
    if not catalysts: catalysts.append("no strong public catalyst detected by screen")

    return {
        "symbol": safe_symbol(symbol),
        "yf_symbol": ns_symbol(symbol),
        "company": info.get("longName") or info.get("shortName") or safe_symbol(symbol),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "close": close,
        "open": openp,
        "day_high": high,
        "day_low": low,
        "volume": int(vol),
        "rel_volume": rel_vol,
        "gap_pct": gap_pct,
        "ret_1d_pct": float(last.get("RET1D", 0) or 0),
        "ret_5d_pct": float(last.get("RET5D", 0) or 0),
        "ret_20d_pct": ret20,
        "relative_strength_20d": rel_strength,
        "rsi14": rsi14,
        "macd_hist": float(last.get("MACD_HIST", 0) or 0),
        "atr14": atr14,
        "ema9": float(last["EMA9"]),
        "ema20": float(last["EMA20"]),
        "sma50": float(last["SMA50"]),
        "sma200": float(last["SMA200"]),
        "target_price": target_1d,
        "target_pct": target_pct,
        "stop_loss": stop_loss,
        "stop_pct": stop_pct,
        "risk_reward": rr,
        "score": score,
        "probability_pct": probability,
        "action": action,
        "catalysts": "; ".join(catalysts),
        "pe": float(pe) if pe else np.nan,
        "forward_pe": float(info.get("forwardPE")) if info.get("forwardPE") else np.nan,
        "price_to_book": float(info.get("priceToBook")) if info.get("priceToBook") else np.nan,
        "beta": float(beta) if beta else np.nan,
        "roe_pct": float(roe) * 100 if roe else np.nan,
        "profit_margin_pct": float(profit_margin) * 100 if profit_margin else np.nan,
        "revenue_growth_pct": float(revenue_growth) * 100 if revenue_growth else np.nan,
        "debt_to_equity": float(debt_to_equity) if debt_to_equity else np.nan,
        "market_cap_cr": (float(market_cap) / 10_000_000) if market_cap else np.nan,
    }

# -----------------------------------------------------------------------------
# OpenAI analysis
# -----------------------------------------------------------------------------
def llm_review_top10(rows: list[dict], market_context: dict, model: str) -> dict:
    if not OPENAI_API_KEY:
        return {"executive_summary": "OpenAI key not configured. Showing quantitative screen only.", "stock_notes": []}
    client = OpenAI(api_key=OPENAI_API_KEY)
    compact_rows = []
    for r in rows[:10]:
        compact_rows.append({
            "symbol": r["symbol"], "company": r["company"], "sector": r["sector"], "close": round(r["close"], 2),
            "score": round(r["score"], 1), "probability_pct": round(r["probability_pct"], 1),
            "target_price": round(r["target_price"], 2), "stop_loss": round(r["stop_loss"], 2),
            "rsi14": round(r["rsi14"], 1), "rel_volume": round(r["rel_volume"], 2),
            "relative_strength_20d": round(r["relative_strength_20d"], 2), "catalysts": r["catalysts"],
        })
    prompt = f"""
You are an Indian equity research assistant. Review the following quantitative screen output for daily stock-pick candidates.
Do not claim certainty. Do not provide personalized financial advice. Explain the public-data rationale and risks.
Market context: {json.dumps(market_context, ensure_ascii=False)}
Top candidates: {json.dumps(compact_rows, ensure_ascii=False)}

Return JSON with exactly these keys:
executive_summary: 4-6 sentence professional summary.
stock_notes: list of objects, one per symbol, with symbol, setup, why_selected, key_risks, validation_check.
watchouts: list of 5 general risk controls.
"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.25,
            response_format={"type": "json_object"},
            timeout=120,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        return {"executive_summary": f"LLM review failed: {e}", "stock_notes": [], "watchouts": []}

# -----------------------------------------------------------------------------
# PDF/email
# -----------------------------------------------------------------------------
def generate_pdf(rows: pd.DataFrame, llm: dict, market_context: dict, output_path: Path) -> Path:
    doc = SimpleDocTemplate(str(output_path), pagesize=landscape(A4), rightMargin=28, leftMargin=28, topMargin=32, bottomMargin=28)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("Title", parent=styles["Title"], fontSize=22, leading=26, alignment=TA_CENTER, textColor=colors.HexColor("#0f172a"))
    h = ParagraphStyle("H", parent=styles["Heading2"], fontSize=12, leading=15, textColor=colors.HexColor("#047857"), spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=8.5, leading=12, textColor=colors.HexColor("#1e293b"))
    small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=7.2, leading=9.2, textColor=colors.HexColor("#1e293b"))
    story = [Paragraph(f"{APP_ICON} {APP_NAME}", title), Paragraph(f"Daily Top 10 Indian Equity Screen - {datetime.now().strftime('%A, %d %B %Y')}", body), Spacer(1, 8)]
    story.append(Paragraph("Disclaimer", h))
    story.append(Paragraph("This report is generated from public data and model heuristics. It is not investment advice or a guarantee of returns.", body))
    story.append(Paragraph("Market Context", h))
    story.append(Paragraph(escape(textify(market_context)), body))
    story.append(Paragraph("Executive Summary", h))
    story.append(Paragraph(escape(textify(llm.get("executive_summary", ""))), body))
    top = rows.head(10).copy()
    cols = ["symbol", "company", "sector", "close", "target_price", "stop_loss", "score", "probability_pct", "rsi14", "rel_volume", "catalysts"]
    header = [Paragraph(f"<b>{c.replace('_',' ').title()}</b>", small) for c in cols]
    data = [header]
    for _, r in top.iterrows():
        data.append([Paragraph(escape(str(round(r[c], 2) if isinstance(r[c], (float, np.floating)) else r[c])), small) for c in cols])
    table = Table(data, colWidths=[0.65*inch, 1.45*inch, 1.0*inch, 0.62*inch, 0.72*inch, 0.72*inch, 0.55*inch, 0.72*inch, 0.52*inch, 0.55*inch, 2.65*inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f172a")), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cbd5e1")), ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("BACKGROUND", (0,1), (-1,-1), colors.HexColor("#f8fafc")), ("PADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(Paragraph("Top 10 Candidates", h))
    story.append(table)
    notes = llm.get("stock_notes", []) or []
    if notes:
        story.append(Paragraph("LLM Notes", h))
        for note in notes:
            story.append(Paragraph(f"<b>{escape(textify(note.get('symbol','')))}</b>: {escape(textify(note.get('why_selected','')))} Risk: {escape(textify(note.get('key_risks','')))}", body))
    story.append(Spacer(1, 10))
    story.append(Paragraph(escape(CREATOR_FOOTNOTE), body))
    doc.build(story)
    return output_path

def attach_file(msg: MIMEMultipart, path: Path, maintype: str, subtype: str) -> None:
    with open(path, "rb") as f:
        part = MIMEBase(maintype, subtype)
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f"attachment; filename={path.name}")
    msg.attach(part)

def send_email(subject: str, html: str, files: list[Path]) -> dict:
    if not all([SMTP_HOST, SMTP_PORT, SENDER_EMAIL, SENDER_APP_PASSWORD, RECIPIENT_EMAIL]):
        raise RuntimeError("SMTP/email settings are incomplete.")
    size_mb = sum(p.stat().st_size for p in files if p.exists()) / (1024 * 1024)
    if size_mb > MAX_EMAIL_ATTACHMENT_MB:
        raise RuntimeError(f"Attachments exceed {MAX_EMAIL_ATTACHMENT_MB:.0f} MB limit.")
    msg = MIMEMultipart("mixed")
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(re.sub("<[^<]+?>", "", html), "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    for p in files:
        if p.exists():
            attach_file(msg, p, "application", "octet-stream")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as srv:
        srv.ehlo(); srv.starttls(context=ssl.create_default_context()); srv.ehlo()
        srv.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        srv.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_string())
    return {"sent": True, "recipient": RECIPIENT_EMAIL, "attachment_mb": round(size_mb, 2), "sent_at": datetime.now().isoformat(timespec="seconds")}

# -----------------------------------------------------------------------------
# UI controls
# -----------------------------------------------------------------------------
for key, default in [("scan_df", None), ("llm", None), ("market_context", None), ("report_id", None)]:
    st.session_state.setdefault(key, default)

with st.sidebar:
    st.markdown(f"👤 Logged in as **{st.session_state.login_user}**")
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.login_user = ""
        st.rerun()
    st.markdown("---")
    st.markdown("### ⚙️ Scan setup")
    universe_choice = st.selectbox("Universe", list(UNIVERSES.keys()) + ["CUSTOM_MANUAL", "CUSTOM_CSV"], index=(list(UNIVERSES.keys()).index(DEFAULT_UNIVERSE) if DEFAULT_UNIVERSE in UNIVERSES else 1))
    manual_symbols = ""
    uploaded = None
    if universe_choice == "CUSTOM_MANUAL":
        manual_symbols = st.text_area("Symbols, comma/newline separated", "RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN,LT")
    elif universe_choice == "CUSTOM_CSV":
        uploaded = st.file_uploader("Upload CSV with column symbol", type=["csv"])
    max_symbols = st.slider("Max symbols to scan", 10, 200, 75, step=5)
    min_price = st.number_input("Minimum price", value=50.0, min_value=0.0)
    min_volume = st.number_input("Minimum daily volume", value=300000, min_value=0, step=50000)
    model_choice = st.selectbox("OpenAI model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"], index=0)
    use_llm = st.checkbox("Use OpenAI LLM review", value=OPENAI_USE_LLM)
    st.markdown("---")
    st.markdown("### 📧 Email")
    email_ok = all([SMTP_HOST, SMTP_PORT, SENDER_EMAIL, SENDER_APP_PASSWORD, RECIPIENT_EMAIL])
    st.success(f"Configured to {RECIPIENT_EMAIL}" if email_ok else "Set SMTP/email env or secrets")

# Header
c1, c2 = st.columns([3, 1])
with c1:
    st.markdown(f'<div class="pulse-header">{APP_ICON} {APP_NAME}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="pulse-sub">{APP_TAGLINE} · {datetime.now().strftime("%A, %d %B %Y")}</div>', unsafe_allow_html=True)
with c2:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("▶ Run Daily Scan", use_container_width=True)

st.markdown('<div class="warn-card"><b>Risk notice:</b> This app estimates probability from public data, technical indicators, and LLM explanation. It does not predict the market with certainty and is not investment advice.</div>', unsafe_allow_html=True)

# Determine symbols
symbols: list[str] = []
if universe_choice in UNIVERSES:
    symbols = UNIVERSES[universe_choice]
elif universe_choice == "CUSTOM_MANUAL":
    symbols = [safe_symbol(s) for s in re.split(r"[,\n\s]+", manual_symbols) if safe_symbol(s)]
elif universe_choice == "CUSTOM_CSV" and uploaded is not None:
    try:
        dfu = pd.read_csv(uploaded)
        col = "symbol" if "symbol" in dfu.columns else dfu.columns[0]
        symbols = [safe_symbol(str(x)) for x in dfu[col].dropna().tolist() if safe_symbol(str(x))]
    except Exception as e:
        st.error(f"Could not read CSV: {e}")

symbols = list(dict.fromkeys(symbols))[:max_symbols]

if run_btn:
    st.session_state.report_id = run_id()
    if not symbols:
        st.error("No symbols selected.")
        st.stop()

    # Market context
    nifty = fetch_index_history("^NSEI", "1y")
    market_ret_20d = 0.0
    market_context = {"index": "NIFTY 50", "date": today_str(), "source": "Yahoo Finance/yfinance public feed"}
    if not nifty.empty and len(nifty) > 30:
        nifty_ind = add_indicators(nifty.rename(columns={"Adj Close": "AdjClose"}))
        nlast = nifty_ind.iloc[-1]
        market_ret_20d = float(nlast.get("RET20D", 0) or 0)
        market_context.update({
            "nifty_close": round(float(nlast["Close"]), 2),
            "nifty_1d_pct": round(float(nlast.get("RET1D", 0) or 0), 2),
            "nifty_20d_pct": round(market_ret_20d, 2),
            "nifty_trend": "bullish" if float(nlast["Close"]) > float(nlast["EMA20"]) else "cautious/bearish",
        })

    rows = []
    failures = []
    progress = st.progress(0, text="Starting scan...")
    for i, sym in enumerate(symbols, 1):
        progress.progress(i / len(symbols), text=f"Scanning {sym} ({i}/{len(symbols)})")
        try:
            row = calculate_stock_row(sym, market_ret_20d)
            if row and row["close"] >= min_price and row["volume"] >= min_volume:
                rows.append(row)
            else:
                failures.append(sym)
        except Exception:
            failures.append(sym)
        time.sleep(0.02)
    progress.empty()

    if not rows:
        st.error("No valid rows returned. Try fewer symbols, lower filters, or rerun after data provider recovers.")
        st.stop()

    df = pd.DataFrame(rows)
    df = df.sort_values(["score", "probability_pct", "rel_volume", "relative_strength_20d"], ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    st.session_state.scan_df = df
    st.session_state.market_context = market_context

    llm = {"executive_summary": "LLM review skipped. Quantitative screen completed.", "stock_notes": [], "watchouts": []}
    if use_llm:
        with st.spinner("OpenAI reviewing top 10 setups and risks..."):
            llm = llm_review_top10(df.head(10).to_dict("records"), market_context, model_choice)
    st.session_state.llm = llm

    # Persist outputs
    csv_path = rpath("csv", "_top_candidates")
    json_path = rpath("json", "_analysis")
    pdf_path = rpath("pdf", "_report")
    df.to_csv(csv_path, index=False)
    save_json({"market_context": market_context, "llm": llm, "top10": df.head(10).to_dict("records")}, json_path)
    try:
        generate_pdf(df, llm, market_context, pdf_path)
    except Exception as e:
        st.warning(f"PDF generation failed: {e}")

# -----------------------------------------------------------------------------
# Results display
# -----------------------------------------------------------------------------
df = st.session_state.scan_df
llm = st.session_state.llm or {}
market_context = st.session_state.market_context or {}

if df is not None and not df.empty:
    top10 = df.head(10).copy()
    mcols = st.columns(5)
    values = [
        ("Scanned / Passed", f"{len(df)}"),
        ("Top Score", f"{top10.iloc[0]['score']:.1f}"),
        ("Top Probability", f"{top10.iloc[0]['probability_pct']:.1f}%"),
        ("NIFTY 20D", f"{market_context.get('nifty_20d_pct', 0)}%"),
        ("Run ID", active_report_id()),
    ]
    for col, (label, val) in zip(mcols, values):
        with col:
            st.markdown(f'<div class="metric-box"><div class="metric-val">{escape(str(val))}</div><div class="metric-label">{escape(label)}</div></div>', unsafe_allow_html=True)

    st.markdown("### 🧠 Executive AI Review")
    st.markdown(f'<div class="card">{escape(textify(llm.get("executive_summary", ""))).replace(chr(10), "<br>")}</div>', unsafe_allow_html=True)

    display_cols = [
        "rank", "symbol", "company", "sector", "close", "target_price", "target_pct", "stop_loss", "stop_pct", "risk_reward",
        "probability_pct", "score", "action", "rsi14", "rel_volume", "relative_strength_20d", "catalysts",
    ]
    styled = top10[display_cols].copy()
    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={
            "close": st.column_config.NumberColumn("Close", format="₹%.2f"),
            "target_price": st.column_config.NumberColumn("Target", format="₹%.2f"),
            "target_pct": st.column_config.NumberColumn("Target %", format="%.2f%%"),
            "stop_loss": st.column_config.NumberColumn("Stop", format="₹%.2f"),
            "stop_pct": st.column_config.NumberColumn("Risk %", format="%.2f%%"),
            "probability_pct": st.column_config.ProgressColumn("Probability", min_value=0, max_value=100, format="%.1f%%"),
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
        },
    )

    tabs = st.tabs(["📊 Top Stock Detail", "📈 Chart", "🧪 Full Data", "📄 Downloads & Email"])
    with tabs[0]:
        notes = {n.get("symbol"): n for n in (llm.get("stock_notes", []) or []) if isinstance(n, dict)}
        for _, row in top10.iterrows():
            css = "buy" if row["action"].startswith("BUY") else ("watch" if row["action"].startswith("WATCH") else "avoid")
            note = notes.get(row["symbol"], {})
            st.markdown(f"""
<div class="card">
<b>{int(row['rank'])}. {escape(row['symbol'])} - {escape(row['company'])}</b>
<span class="badge {css}">{escape(row['action'])}</span><br>
<span class="small-note">Sector: {escape(textify(row['sector']))} · Close: {money(row['close'])} · Target: {money(row['target_price'])} · Stop: {money(row['stop_loss'])} · Probability: {row['probability_pct']:.1f}%</span><br><br>
<b>Quant setup:</b> {escape(row['catalysts'])}<br>
<b>LLM rationale:</b> {escape(textify(note.get('why_selected', 'Not reviewed by LLM.')))}<br>
<b>Risks:</b> {escape(textify(note.get('key_risks', 'Validate market trend, news, volume, and stop-loss before entry.')))}
</div>
""", unsafe_allow_html=True)

    with tabs[1]:
        selected = st.selectbox("Select symbol", top10["symbol"].tolist())
        hist = fetch_history(selected, period="1y")
        if not hist.empty:
            hi = add_indicators(hist)
            fig = go.Figure()
            fig.add_trace(go.Candlestick(x=hi["Date"], open=hi["Open"], high=hi["High"], low=hi["Low"], close=hi["Close"], name="OHLC"))
            fig.add_trace(go.Scatter(x=hi["Date"], y=hi["EMA20"], name="EMA20"))
            fig.add_trace(go.Scatter(x=hi["Date"], y=hi["SMA50"], name="SMA50"))
            fig.add_trace(go.Scatter(x=hi["Date"], y=hi["SMA200"], name="SMA200"))
            fig.update_layout(height=560, margin=dict(l=20, r=20, t=30, b=20), xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

    with tabs[2]:
        st.dataframe(df, use_container_width=True, hide_index=True)

    with tabs[3]:
        csv_path = rpath("csv", "_top_candidates")
        pdf_path = rpath("pdf", "_report")
        json_path = rpath("json", "_analysis")
        c1, c2, c3 = st.columns(3)
        if csv_path.exists():
            c1.download_button("⬇️ Download CSV", csv_path.read_bytes(), file_name=csv_path.name, mime="text/csv", use_container_width=True)
        if pdf_path.exists():
            c2.download_button("⬇️ Download PDF", pdf_path.read_bytes(), file_name=pdf_path.name, mime="application/pdf", use_container_width=True)
        if json_path.exists():
            c3.download_button("⬇️ Download JSON", json_path.read_bytes(), file_name=json_path.name, mime="application/json", use_container_width=True)

        st.markdown("### 📧 Email report")
        if not email_ok:
            st.info("Configure SMTP settings to enable email delivery.")
        if st.button("Send Email Now", disabled=not email_ok, use_container_width=True):
            try:
                html = f"""
                <h2>{APP_ICON} {APP_NAME}</h2>
                <p>Attached are the daily Indian equity screen outputs for {today_str()}.</p>
                <p><b>Disclaimer:</b> This is public-data research support and not investment advice.</p>
                <p>{escape(textify(llm.get('executive_summary', '')))}</p>
                """
                result = send_email(f"{APP_NAME} Daily Top 10 | {today_str()}", html, [p for p in [csv_path, pdf_path] if p.exists()])
                st.success(f"Email sent to {result['recipient']} · attachments {result['attachment_mb']} MB")
            except Exception as e:
                st.error(f"Email failed: {e}")
else:
    st.markdown(f"""
<div style="text-align:center;padding:4rem 2rem;"><div style="font-size:4.5rem;">{APP_ICON}</div>
<div style="font-size:1.4rem;color:#7a8ca5;margin-top:1rem;">Choose a universe and click <b style="color:#22c55e;">▶ Run Daily Scan</b></div>
<div class="small-note" style="margin-top:1rem;">PUBLIC DATA → TECHNICAL SCREEN → FUNDAMENTAL SNAPSHOT → OPENAI REVIEW → TOP 10</div></div>
""", unsafe_allow_html=True)

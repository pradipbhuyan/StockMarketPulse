"""
TelecomPulse AI - Telecom Intelligence Briefing
Fixed delivery build with:
- safer credential loading from Streamlit secrets or environment variables
- automatic email delivery with persistent status/error logging
- manual email delivery button after PDF/MP3 generation
- Gmail attachment-size guard so weekly MP3 does not silently break delivery
- downloadable email HTML/TXT assets

Required env vars or .streamlit/secrets.toml keys:
OPENAI_API_KEY
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your_sender@gmail.com
SENDER_APP_PASSWORD=your_gmail_app_password
RECIPIENT_EMAIL=recipient@example.com
ADMIN_PASSWORD=choose_admin_password
PRADIP_PASSWORD=choose_pradip_password
"""

import asyncio
import json
import os
import re
import smtplib
import ssl
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Any

import edge_tts
import openai
import streamlit as st

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# -----------------------------------------------------------------------------
# Configuration helpers
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
SMTP_HOST = get_config_value("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_config_value("SMTP_PORT", "587") or "587")
SENDER_EMAIL = get_config_value("SENDER_EMAIL")
SENDER_APP_PASSWORD = get_config_value("SENDER_APP_PASSWORD")
RECIPIENT_EMAIL = get_config_value("RECIPIENT_EMAIL")

DEFAULT_MODEL = get_config_value("DEFAULT_MODEL", "gpt-4o-mini")
DEFAULT_TTS_VOICE_LABEL = get_config_value("DEFAULT_TTS_VOICE_LABEL", "en-US-GuyNeural (Male, US)")
CREATOR_FOOTNOTE = "Content created by Pradip Bhuyan, Head of Delivery, TMT."
MAX_EMAIL_ATTACHMENT_MB = float(get_config_value("MAX_EMAIL_ATTACHMENT_MB", "22"))
AUTO_EMAIL_AFTER_GENERATION = get_config_value("AUTO_EMAIL_AFTER_GENERATION", "true").lower() in {"1", "true", "yes", "y"}
ADMIN_PASSWORD = get_config_value("ADMIN_PASSWORD")
PRADIP_PASSWORD = get_config_value("PRADIP_PASSWORD")

# -----------------------------------------------------------------------------
# Streamlit page
# -----------------------------------------------------------------------------
st.set_page_config(page_title="TelecomPulse AI", page_icon="📡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');
:root { --bg:#0a0e1a; --surface:#111827; --border:#1e2d45; --accent:#00d4ff; --accent2:#7c3aed; --text:#e2e8f0; --muted:#64748b; --success:#10b981; --warning:#f59e0b; }
html, body, [data-testid="stAppViewContainer"] { background-color:var(--bg)!important; color:var(--text)!important; font-family:'Syne',sans-serif; }
[data-testid="stSidebar"] { background:var(--surface)!important; border-right:1px solid var(--border)!important; }
.stButton>button { background:linear-gradient(135deg,var(--accent),var(--accent2))!important; color:white!important; border:none!important; font-family:'Syne',sans-serif!important; font-weight:700!important; border-radius:6px!important; padding:0.5rem 1.5rem!important; }
.stButton>button:hover { opacity:0.85!important; }
.pipeline { display:flex; align-items:center; background:var(--surface); border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-bottom:1.5rem; }
.stage { flex:1; text-align:center; padding:0.6rem 0.5rem; font-family:'Space Mono',monospace; font-size:0.63rem; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted); border-right:1px solid var(--border); }
.stage:last-child { border-right:none; } .stage.active{background:linear-gradient(135deg,#0d1f35,#162032);color:var(--accent);} .stage.done{background:#0d1f1a;color:var(--success);} .stage.pending{color:#2a3a4a;}
.score-badge { display:inline-block; font-family:'Space Mono',monospace; font-size:0.75rem; padding:3px 10px; border-radius:4px; font-weight:700; margin-left:8px; }
.score-high{background:#0d2e1f;color:#10b981;border:1px solid #064e3b;} .score-mid{background:#2d1f00;color:#f59e0b;border:1px solid #78350f;} .score-low{background:#2d0f0f;color:#ef4444;border:1px solid #7f1d1d;}
.review-card{background:#131b0d;border:1px solid #2a4a1f;border-left:3px solid var(--success);border-radius:8px;padding:1rem 1.25rem;margin-bottom:1rem;} .review-issue{background:#1a1208;border:1px solid #4a3410;border-left:3px solid var(--warning);border-radius:8px;padding:1rem 1.25rem;margin-bottom:0.6rem;}
.section-card{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:8px;padding:1.25rem 1.5rem;margin-bottom:1.25rem;} .section-title{font-family:'Syne',sans-serif;font-weight:700;font-size:1rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--accent);margin-bottom:0.75rem;} .report-body{font-family:'Syne',sans-serif;font-size:0.95rem;line-height:1.85;color:var(--text);}
.metric-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1rem;text-align:center;} .metric-val{font-family:'Space Mono',monospace;font-size:1.6rem;font-weight:700;color:var(--accent);} .metric-label{font-size:0.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em;}
.approved-banner{background:linear-gradient(135deg,#064e3b,#065f46);border:1px solid #10b981;border-radius:8px;padding:0.75rem 1.25rem;margin-bottom:1.5rem;font-family:'Space Mono',monospace;font-size:0.78rem;color:#6ee7b7;letter-spacing:0.04em;}
.pulse-header{font-family:'Syne',sans-serif;font-weight:800;font-size:2.6rem;background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:0;line-height:1.1;} .pulse-sub{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--muted);letter-spacing:0.15em;text-transform:uppercase;margin-top:4px;}
.status-line{font-family:'Space Mono',monospace;font-size:0.7rem;color:var(--muted);padding:4px 0;letter-spacing:0.04em;} .tag{display:inline-block;font-family:'Space Mono',monospace;font-size:0.65rem;padding:2px 8px;border-radius:3px;margin-right:6px;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.08em;background:#3a2f1e;color:#fbbf24;border:1px solid #92400e;}
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Login gate
# -----------------------------------------------------------------------------
def require_login() -> None:
    """Simple local Streamlit login gate. Passwords are read from secrets/env."""
    allowed_users = {
        "admin": ADMIN_PASSWORD,
        "pradip": PRADIP_PASSWORD,
    }

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "login_user" not in st.session_state:
        st.session_state.login_user = ""

    if st.session_state.authenticated:
        return

    st.markdown('<div class="pulse-header" style="text-align:center;margin-top:3rem;">📡 TelecomPulse AI</div>', unsafe_allow_html=True)
    st.markdown('<div class="pulse-sub" style="text-align:center;">Secure Executive Intelligence Briefing</div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    login_box = st.container()
    with login_box:
        c1, c2, c3 = st.columns([1, 1.2, 1])
        with c2:
            st.markdown("### 🔐 Sign in")
            with st.form("login_form", clear_on_submit=False):
                username = st.text_input("Username", placeholder="admin or pradip").strip().lower()
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Login", use_container_width=True)

            missing_passwords = [u for u, pwd in allowed_users.items() if not pwd]
            if missing_passwords:
                st.warning(
                    "Set these password keys in .streamlit/secrets.toml before login: "
                    + ", ".join(f"{u.upper()}_PASSWORD" for u in missing_passwords)
                )

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
# Paths and constants
# -----------------------------------------------------------------------------
REPORT_DIR = Path("reports")
REPORT_DIR.mkdir(exist_ok=True)

DOMAIN_SECTIONS = [
    "bss_monetization_cx", "oss_automation_assurance", "ran_network_modernization", "core_apis_monetization",
    "ai_data_autonomous_networks", "cloud_edge_private_networks", "security_regulation_spectrum", "satellite_broadband_infrastructure",
]
DOMAIN_LABELS = {
    "bss_monetization_cx": ("💼 BSS, Monetization & CX", "tag-bss"),
    "oss_automation_assurance": ("⚙️ OSS, Automation & Assurance", "tag-oss"),
    "ran_network_modernization": ("📡 RAN & Network Modernization", "tag-ran"),
    "core_apis_monetization": ("🔌 5G Core, APIs & Monetization", "tag-strategy"),
    "ai_data_autonomous_networks": ("🤖 AI, Data & Autonomous Networks", "tag-ai"),
    "cloud_edge_private_networks": ("☁️ Cloud, Edge & Private Networks", "tag-strategy"),
    "security_regulation_spectrum": ("🛡️ Security, Regulation & Spectrum", "tag-strategy"),
    "satellite_broadband_infrastructure": ("🛰️ Satellite, Broadband & Infrastructure", "tag-strategy"),
}

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def active_report_id() -> str:
    if "report_id" not in st.session_state or not st.session_state.report_id:
        st.session_state.report_id = today_str()
    return st.session_state.report_id

def rpath(ext: str, suffix: str = "") -> Path:
    return REPORT_DIR / f"telecom_pulse_{active_report_id()}{suffix}.{ext}"

def pdf_path() -> Path:
    return rpath("pdf", "_final_report")

def email_html_path() -> Path:
    return rpath("html", "_email_brief")

def email_text_path() -> Path:
    return rpath("txt", "_email_brief")

def delivery_status_path() -> Path:
    return rpath("json", "_delivery_status")

def load_json(path: Any) -> Any:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None

def save_json(data: Any, path: Any) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def score_cls(s: Any) -> str:
    try:
        s = int(s)
    except Exception:
        s = 0
    return "score-high" if s >= 80 else ("score-mid" if s >= 60 else "score-low")

def safe_html(text: Any) -> str:
    return escape(str(text or "")).replace("\n", "<br>")

# -----------------------------------------------------------------------------
# Prompts
# -----------------------------------------------------------------------------
DRAFT_PROMPT = """You are TelecomPulse, a senior telecom analyst and broadcast journalist covering the global telecommunications industry.
Today is __DATE__.
Briefing type: __BRIEFING_TYPE__.

Generate a premium telecom intelligence briefing for executives. Prioritize fresh variety and strategic relevance.

Core coverage areas: top telecom developments, market signals, BSS/CX, OSS/assurance, RAN/Open RAN, 5G Core/APIs, AI/autonomous networks, cloud/edge/private networks, security/regulation/spectrum, satellite/NTN/broadband/fiber/infrastructure, deals and partnerships, analyst take.

QUALITY STANDARDS:
- Use real telecom industry context, standards bodies, vendors, operators and market dynamics.
- Reference relevant entities such as Amdocs, Ericsson, Nokia, Huawei, ZTE, Netcracker, CSG, Oracle, IBM, Accenture, Capgemini, AWS, Microsoft, Google Cloud, Red Hat, TM Forum, 3GPP, ETSI, O-RAN Alliance, GSMA, CAMARA, ITU and major global operators where relevant.
- Do not invent specific deal values or contract wins unless widely known. If exact value is not known, use "Not disclosed".
- Write at Gartner/Omdia/Heavy Reading analyst-advisory level.

Return a JSON object with exactly these keys:
"briefing_type", "executive_summary", "top_developments", "market_signals_vendor_moves", "bss_monetization_cx", "oss_automation_assurance", "ran_network_modernization", "core_apis_monetization", "ai_data_autonomous_networks", "cloud_edge_private_networks", "security_regulation_spectrum", "satellite_broadband_infrastructure", "key_deals", "analyst_take", "what_to_watch_next", "tts_script".

top_developments: list of 5 objects with title, why_it_matters, region, domain.
key_deals: list of objects with title, parties, value, domain, significance.
tts_script must be an empty string.
Length guidance: __LENGTH_GUIDANCE__
Return ONLY valid JSON. No markdown fences.
"""

REVIEW_PROMPT = """You are the Chief Editorial Officer of TelecomPulse.
Review the draft across content clarity, authenticity and professionalism.
For each section, score 0-100 and provide issues and suggestions. Produce a revised version of every section scoring below 92. For sections scoring 92+, omit from revised.
Draft: __DRAFT__
Return JSON with exactly: overall_score, overall_verdict, reviews, revised, editorial_summary.
Return ONLY valid JSON.
"""

TTS_PROMPT = """You are TelecomPulse, a professional broadcast scriptwriter for senior telecom executives.
Today is __DATE__. Briefing type: __BRIEFING_TYPE__.
Using the approved report below, write a spoken audio briefing script.
APPROVED REPORT: __REPORT__
Requirements: __TTS_LENGTH_GUIDANCE__
Spoken broadcast style, no bullets, no markdown headings, smooth transitions.
Return JSON with exactly: tts_script. Return ONLY valid JSON.
"""

# -----------------------------------------------------------------------------
# OpenAI
# -----------------------------------------------------------------------------
def call_openai(client: openai.OpenAI, prompt: str, model: str, max_tokens: int = 8000) -> dict:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.55,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            timeout=180,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("OpenAI returned an empty response.")
        return json.loads(content)
    except openai.AuthenticationError as e:
        raise RuntimeError("OpenAI authentication failed. Check OPENAI_API_KEY.") from e
    except openai.RateLimitError as e:
        raise RuntimeError("OpenAI rate limit or quota issue. Check billing/quota.") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(f"Could not connect to OpenAI API: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI returned non-JSON content: {e}") from e

def briefing_length_guidance(briefing_type: str) -> str:
    if briefing_type.startswith("Daily"):
        return "Daily Flash Brief: 900-1,300 words total. Key deals: 3-5."
    return "Weekly Intelligence Brief: 3,500-5,000 words total. Key deals: 8-12."

def tts_length_guidance(briefing_type: str) -> str:
    if briefing_type.startswith("Daily"):
        return "Target length: 700 to 1,000 words, suitable for 5-8 minutes."
    return "Target length: 4,000 to 5,500 words, suitable for 30-40 minutes."

def generate_draft(client: openai.OpenAI, model: str, briefing_type: str) -> dict:
    prompt = DRAFT_PROMPT.replace("__DATE__", datetime.now().strftime("%A, %B %d, %Y")).replace("__BRIEFING_TYPE__", briefing_type).replace("__LENGTH_GUIDANCE__", briefing_length_guidance(briefing_type))
    return call_openai(client, prompt, model, max_tokens=10000 if briefing_type.startswith("Weekly") else 5000)

def review_draft(client: openai.OpenAI, model: str, draft: dict) -> dict:
    slim = {k: v for k, v in draft.items() if k != "tts_script"}
    return call_openai(client, REVIEW_PROMPT.replace("__DRAFT__", json.dumps(slim, indent=2, ensure_ascii=False)), model, max_tokens=8000)

def build_final(draft: dict, review: dict | None) -> dict:
    final = dict(draft)
    if review:
        for k, v in review.get("revised", {}).items():
            if isinstance(v, str) and v.strip():
                final[k] = v
    return final

def generate_tts_script(client: openai.OpenAI, model: str, final: dict, briefing_type: str) -> str:
    report_for_tts = {k: v for k, v in final.items() if k != "tts_script"}
    prompt = TTS_PROMPT.replace("__DATE__", datetime.now().strftime("%A, %B %d, %Y")).replace("__BRIEFING_TYPE__", briefing_type).replace("__REPORT__", json.dumps(report_for_tts, indent=2, ensure_ascii=False)).replace("__TTS_LENGTH_GUIDANCE__", tts_length_guidance(briefing_type))
    result = call_openai(client, prompt, model, max_tokens=12000 if briefing_type.startswith("Weekly") else 3000)
    return result.get("tts_script", "")

# -----------------------------------------------------------------------------
# TTS and Email
# -----------------------------------------------------------------------------
VOICES = {
    "en-US-GuyNeural (Male, US)": "en-US-GuyNeural",
    "en-US-JennyNeural (Female, US)": "en-US-JennyNeural",
    "en-GB-RyanNeural (Male, UK)": "en-GB-RyanNeural",
    "en-GB-SoniaNeural (Female, UK)": "en-GB-SoniaNeural",
    "en-AU-WilliamNeural (Male, AU)": "en-AU-WilliamNeural",
}

async def _tts(text: str, voice: str, path: str):
    await edge_tts.Communicate(text, voice).save(path)

def generate_mp3(text: str, voice: str, path: str):
    asyncio.run(_tts(text, voice, path))

def email_subject(report: dict) -> str:
    return f"TelecomPulse {report.get('briefing_type', 'Telecom Intelligence Briefing')} | {today_str()}"

def _plain_preview(text: str, max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:max_chars] + ("..." if len(text) > max_chars else "")

def build_email_summary(report: dict) -> dict:
    highlights = []
    for item in (report.get("top_developments", []) or [])[:5]:
        if isinstance(item, dict):
            highlights.append({"title": item.get("title", ""), "detail": item.get("why_it_matters", ""), "meta": " | ".join(x for x in [item.get("region", ""), item.get("domain", "")] if x)})
    if not highlights:
        for deal in (report.get("key_deals", []) or [])[:5]:
            highlights.append({"title": deal.get("title", ""), "detail": deal.get("significance", ""), "meta": deal.get("domain", "")})
    return {
        "intro": "Please find attached the latest TelecomPulse Intelligence Briefing, prepared for senior telecom stakeholders and delivery leaders.",
        "highlights": highlights[:5],
        "why_it_matters": _plain_preview(report.get("analyst_take", ""), 520) or _plain_preview(report.get("executive_summary", ""), 520),
    }

def build_professional_email_html(report: dict, pdf_attached: bool = True, mp3_attached: bool = True, mp3_omitted_reason: str = "") -> str:
    summary = build_email_summary(report)
    highlights_html = "".join(f"""
    <tr><td style='padding:12px 0;border-bottom:1px solid #e5e7eb;vertical-align:top;width:28px;'><div style='width:22px;height:22px;border-radius:50%;background:#e0f2fe;color:#0369a1;text-align:center;line-height:22px;font-family:Arial;font-size:12px;font-weight:700;'>{i}</div></td>
    <td style='padding:12px 0 12px 10px;border-bottom:1px solid #e5e7eb;'><div style='font-family:Arial;font-size:15px;font-weight:700;color:#0f172a;'>{escape(str(h.get('title','')))}</div><div style='font-family:Arial;font-size:12px;color:#64748b;margin-top:2px;'>{escape(str(h.get('meta','')))}</div><div style='font-family:Arial;font-size:13px;color:#334155;line-height:19px;margin-top:6px;'>{escape(str(h.get('detail','')))}</div></td></tr>
    """ for i, h in enumerate(summary["highlights"], 1))
    attachments = []
    if pdf_attached: attachments.append("Professional PDF report")
    if mp3_attached: attachments.append("MP3 audio briefing")
    if mp3_omitted_reason: attachments.append(f"MP3 not attached ({mp3_omitted_reason})")
    attachment_text = ", ".join(attachments) if attachments else "The briefing materials"
    return f"""<!doctype html><html><head><meta charset='utf-8'></head><body style='margin:0;padding:0;background:#f1f5f9;'>
<table width='100%' style='background:#f1f5f9;padding:28px 0;'><tr><td align='center'><table width='720' style='width:720px;max-width:94%;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #dbe3ef;'>
<tr><td style='padding:30px 34px;background:#0f172a;color:white;'><div style='font-family:Arial;font-size:28px;font-weight:800;'>📡 TelecomPulse</div><div style='font-family:Arial;font-size:11px;letter-spacing:2px;color:#9bdcf5;text-transform:uppercase;'>Executive Intelligence Brief</div><div style='font-family:Arial;color:#cbd5e1;font-size:13px;margin-top:16px;'>{escape(str(report.get('briefing_type','Telecom Intelligence Briefing')))} · {escape(datetime.now().strftime('%A, %B %d, %Y'))}</div></td></tr>
<tr><td style='padding:30px 34px;'><p style='font-family:Arial;font-size:15px;color:#334155;'>Hi,</p><p style='font-family:Arial;font-size:15px;line-height:24px;color:#334155;'>{escape(summary['intro'])}</p>
<div style='background:#f8fafc;border:1px solid #e2e8f0;border-left:5px solid #0ea5e9;border-radius:12px;padding:18px 20px;margin:22px 0;'><div style='font-family:Arial;font-size:12px;font-weight:800;letter-spacing:1.4px;text-transform:uppercase;color:#0369a1;margin-bottom:8px;'>Executive Summary</div><div style='font-family:Arial;font-size:15px;line-height:24px;color:#1e293b;'>{safe_html(report.get('executive_summary',''))}</div></div>
<div style='font-family:Arial;font-size:18px;font-weight:800;color:#0f172a;margin:26px 0 8px;'>Top highlights</div><table width='100%'>{highlights_html}</table>
<div style='font-family:Arial;font-size:18px;font-weight:800;color:#0f172a;margin:28px 0 10px;'>Why it matters</div><p style='font-family:Arial;font-size:14px;line-height:23px;color:#334155;'>{escape(summary['why_it_matters'])}</p>
<div style='background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:16px 18px;margin:24px 0;'><div style='font-family:Arial;font-size:14px;line-height:22px;color:#312e81;'><strong>Attached:</strong> {escape(attachment_text)}.</div></div>
<p style='font-family:Arial;font-size:15px;line-height:23px;color:#334155;'>Regards,<br><strong>Pradip Bhuyan</strong><br>Head of Delivery, TMT</p></td></tr>
<tr><td style='padding:18px 34px;background:#0f172a;'><div style='font-family:Arial;font-size:11px;line-height:17px;color:#94a3b8;'>{escape(CREATOR_FOOTNOTE)}</div></td></tr></table></td></tr></table></body></html>"""

def build_professional_email_text(report: dict, mp3_omitted_reason: str = "") -> str:
    summary = build_email_summary(report)
    lines = [f"Subject: {email_subject(report)}", "", "Hi,", "", summary["intro"], "", "EXECUTIVE SUMMARY", str(report.get("executive_summary", "")).strip(), "", "TOP HIGHLIGHTS"]
    for i, h in enumerate(summary["highlights"], 1):
        meta = f" ({h.get('meta')})" if h.get("meta") else ""
        lines += [f"{i}. {h.get('title','')}{meta}", f"   {h.get('detail','')}"]
    attachment_line = "Attached: Professional PDF report and MP3 audio briefing."
    if mp3_omitted_reason:
        attachment_line = f"Attached: Professional PDF report. MP3 not attached: {mp3_omitted_reason}."
    lines += ["", "WHY IT MATTERS", summary["why_it_matters"], "", attachment_line, "", "Regards,", "Pradip Bhuyan", "Head of Delivery, TMT", "", CREATOR_FOOTNOTE]
    return "\n".join(lines)

def save_email_assets(report: dict, html_path: str, text_path: str, pdf_attached: bool = True, mp3_attached: bool = True, mp3_omitted_reason: str = ""):
    html = build_professional_email_html(report, pdf_attached, mp3_attached, mp3_omitted_reason)
    text = build_professional_email_text(report, mp3_omitted_reason)
    Path(html_path).write_text(html, encoding="utf-8")
    Path(text_path).write_text(text, encoding="utf-8")
    return html, text

def attachment_size_mb(paths: list[str]) -> float:
    total = 0
    for p in paths:
        if p and os.path.exists(p):
            total += os.path.getsize(p)
    return total / (1024 * 1024)

def attach_file(msg: MIMEMultipart, file_path: str, maintype: str, subtype: str, filename: str) -> None:
    with open(file_path, "rb") as f:
        part = MIMEBase(maintype, subtype)
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f"attachment; filename={filename}")
    msg.attach(part)

def send_email(cfg: dict, report: dict, mp3_path: str | None, pdf_report_path: str | None = None, include_mp3: bool = True) -> dict:
    if not all([cfg.get("smtp_host"), cfg.get("smtp_port"), cfg.get("sender"), cfg.get("password"), cfg.get("recipient")]):
        raise RuntimeError("SMTP/email settings are incomplete.")

    pdf_exists = bool(pdf_report_path and os.path.exists(pdf_report_path))
    mp3_exists = bool(mp3_path and os.path.exists(mp3_path))
    mp3_omitted_reason = ""
    files_to_attach = []
    if pdf_exists:
        files_to_attach.append(str(pdf_report_path))
    if include_mp3 and mp3_exists:
        prospective = files_to_attach + [str(mp3_path)]
        # Base64 inflates attachments by roughly 33%; keep a conservative pre-encoding cap.
        if attachment_size_mb(prospective) <= MAX_EMAIL_ATTACHMENT_MB:
            files_to_attach.append(str(mp3_path))
        else:
            mp3_omitted_reason = f"combined attachment size exceeds {MAX_EMAIL_ATTACHMENT_MB:.0f} MB safe email limit"

    msg = MIMEMultipart("mixed")
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg["Subject"] = email_subject(report)
    html = build_professional_email_html(report, pdf_attached=pdf_exists, mp3_attached=(mp3_exists and str(mp3_path) in files_to_attach), mp3_omitted_reason=mp3_omitted_reason)
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(build_professional_email_text(report, mp3_omitted_reason), "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alternative)

    if pdf_exists:
        attach_file(msg, str(pdf_report_path), "application", "pdf", f"telecom_pulse_{active_report_id()}_final_report.pdf")
    if mp3_exists and str(mp3_path) in files_to_attach:
        attach_file(msg, str(mp3_path), "audio", "mpeg", f"telecom_pulse_{active_report_id()}.mp3")

    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=60) as srv:
        srv.ehlo()
        srv.starttls(context=ssl.create_default_context())
        srv.ehlo()
        srv.login(cfg["sender"], cfg["password"])
        refused = srv.sendmail(cfg["sender"], [cfg["recipient"]], msg.as_string())
        if refused:
            raise RuntimeError(f"SMTP refused recipients: {refused}")

    result = {
        "sent": True,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "recipient": cfg["recipient"],
        "pdf_attached": pdf_exists,
        "mp3_attached": mp3_exists and str(mp3_path) in files_to_attach,
        "mp3_omitted_reason": mp3_omitted_reason,
        "attachment_mb_before_base64": round(attachment_size_mb(files_to_attach), 2),
    }
    save_json(result, delivery_status_path())
    return result

def mark_email_failure(err: Exception) -> None:
    save_json({"sent": False, "failed_at": datetime.now().isoformat(timespec="seconds"), "error": str(err)}, delivery_status_path())

# -----------------------------------------------------------------------------
# PDF
# -----------------------------------------------------------------------------
def _clean_for_pdf(text: str) -> str:
    if text is None:
        return ""
    lines = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            lines.append(f"<b>{escape(stripped[3:].strip())}</b>")
        elif stripped:
            lines.append(escape(stripped))
        else:
            lines.append("")
    return "<br/>".join(lines)

def generate_pdf_report(report: dict, output_path: str):
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=44, leftMargin=44, topMargin=52, bottomMargin=44, title=f"TelecomPulse AI Briefing - {today_str()}", author="Pradip Bhuyan")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=25, leading=30, alignment=TA_CENTER, textColor=colors.HexColor("#0F172A"), spaceAfter=4)
    logo_style = ParagraphStyle("Logo", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=TA_CENTER, textColor=colors.HexColor("#0369A1"), spaceAfter=12)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, alignment=TA_CENTER, textColor=colors.HexColor("#64748B"), spaceAfter=16)
    h_style = ParagraphStyle("Heading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=colors.HexColor("#0369A1"), spaceBefore=13, spaceAfter=7)
    body_style = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=14, textColor=colors.HexColor("#1E293B"), alignment=TA_LEFT, spaceAfter=8)
    small_style = ParagraphStyle("Small", parent=styles["BodyText"], fontName="Helvetica", fontSize=8, leading=11, textColor=colors.HexColor("#475569"))
    footer_style = ParagraphStyle("Footer", parent=styles["BodyText"], fontName="Helvetica-Oblique", fontSize=8.5, leading=12, textColor=colors.HexColor("#475569"), alignment=TA_CENTER)

    story = []
    logo_table = Table([[Paragraph("📡", ParagraphStyle("Icon", parent=styles["Normal"], fontSize=28, alignment=TA_CENTER))]], colWidths=[0.65 * inch], rowHeights=[0.65 * inch], hAlign="CENTER")
    logo_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E0F2FE")), ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#0284C7")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [logo_table, Spacer(1, 8), Paragraph("TelecomPulse AI", title_style), Paragraph("Strategic Telecom Intelligence", logo_style), Paragraph(f"{escape(str(report.get('briefing_type', 'Telecom Intelligence Briefing')))} · {datetime.now().strftime('%A, %B %d, %Y')}", subtitle_style)]
    story += [Paragraph("Executive Summary", h_style), Paragraph(_clean_for_pdf(report.get("executive_summary", "")), body_style)]

    top_devs = report.get("top_developments", []) or []
    if top_devs:
        story.append(Paragraph("Top 5 Telecom Developments", h_style))
        data = [[Paragraph("<b>Development</b>", small_style), Paragraph("<b>Why It Matters</b>", small_style), Paragraph("<b>Region</b>", small_style)]]
        for item in top_devs[:5]:
            if isinstance(item, dict):
                data.append([Paragraph(escape(str(item.get("title", ""))), small_style), Paragraph(escape(str(item.get("why_it_matters", ""))), small_style), Paragraph(escape(str(item.get("region", ""))), small_style)])
        t = Table(data, colWidths=[2.1 * inch, 3.55 * inch, 0.95 * inch], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 5)]))
        story += [t, Spacer(1, 10)]

    sections = [("Market Signals & Vendor Moves", report.get("market_signals_vendor_moves", ""))] + [(DOMAIN_LABELS[k][0], report.get(k, "")) for k in DOMAIN_SECTIONS] + [("Analyst Take", report.get("analyst_take", ""))]
    for heading, content in sections:
        if content:
            story += [Paragraph(escape(heading), h_style), Paragraph(_clean_for_pdf(content), body_style)]

    deals = report.get("key_deals", []) or []
    if deals:
        story += [PageBreak(), Paragraph("Key Deals & Partnerships", h_style)]
        table_data = [[Paragraph("<b>Domain</b>", small_style), Paragraph("<b>Title</b>", small_style), Paragraph("<b>Parties</b>", small_style), Paragraph("<b>Value</b>", small_style), Paragraph("<b>Strategic Significance</b>", small_style)]]
        for deal in deals:
            if isinstance(deal, dict):
                table_data.append([Paragraph(escape(str(deal.get("domain", ""))), small_style), Paragraph(escape(str(deal.get("title", ""))), small_style), Paragraph(escape(str(deal.get("parties", ""))), small_style), Paragraph(escape(str(deal.get("value", ""))), small_style), Paragraph(escape(str(deal.get("significance", ""))), small_style)])
        deals_table = Table(table_data, colWidths=[0.7 * inch, 1.45 * inch, 1.3 * inch, 0.85 * inch, 2.3 * inch], repeatRows=1)
        deals_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 5)]))
        story.append(deals_table)

    watch_items = report.get("what_to_watch_next", []) or []
    if watch_items:
        story.append(Paragraph("What to Watch Next", h_style))
        for idx, item in enumerate(watch_items[:5], 1):
            txt = item.get("title") if isinstance(item, dict) else str(item)
            story.append(Paragraph(f"<b>{idx}.</b> {escape(str(txt))}", body_style))
    story += [Spacer(1, 18), Paragraph(CREATOR_FOOTNOTE, footer_style)]

    def add_page_number(canvas, doc):
        canvas.saveState(); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.HexColor("#64748B")); canvas.drawRightString(A4[0] - 44, 26, f"TelecomPulse AI · {today_str()} · Page {doc.page}"); canvas.restoreState()
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return output_path

# -----------------------------------------------------------------------------
# Session state and sidebar
# -----------------------------------------------------------------------------
for k, default in [("stage", None), ("draft", None), ("review", None), ("final", None), ("mp3_ready", False), ("email_sent", False), ("email_last_error", ""), ("report_id", None)]:
    if k not in st.session_state:
        st.session_state[k] = default

with st.sidebar:
    st.markdown(f"👤 Logged in as **{st.session_state.login_user}**")
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.login_user = ""
        st.rerun()
    st.markdown("---")
    st.markdown("### ⚙️ Configuration")
    st.markdown("---")
    st.markdown("**🔑 OpenAI**")
    st.success("OpenAI key loaded" if OPENAI_API_KEY else "Set OPENAI_API_KEY in secrets/env")
    briefing_type = st.selectbox("Briefing Type", ["Weekly Intelligence Brief", "Daily Flash Brief"], index=0)
    model_options = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
    default_model_index = model_options.index(DEFAULT_MODEL) if DEFAULT_MODEL in model_options else 1
    model_choice = st.selectbox("Model", model_options, index=default_model_index)
    st.markdown("---")
    st.markdown("**🔊 Audio Voice**")
    voice_labels = list(VOICES.keys())
    default_voice_index = voice_labels.index(DEFAULT_TTS_VOICE_LABEL) if DEFAULT_TTS_VOICE_LABEL in voice_labels else 0
    voice_label = st.selectbox("TTS Voice", voice_labels, index=default_voice_index)
    voice = VOICES[voice_label]
    st.markdown("---")
    st.markdown("**📧 Email Delivery**")
    email_configured_now = all([SMTP_HOST, SMTP_PORT, SENDER_EMAIL, SENDER_APP_PASSWORD, RECIPIENT_EMAIL])
    st.success(f"Configured to {RECIPIENT_EMAIL}" if email_configured_now else "Set SMTP/email secrets/env")
    auto_email_enabled = st.checkbox("Automatically email after generation", value=AUTO_EMAIL_AFTER_GENERATION)
    include_mp3_email = st.checkbox("Attach MP3 when size allows", value=True)
    st.caption(f"MP3 skipped if PDF+MP3 exceeds {MAX_EMAIL_ATTACHMENT_MB:.0f} MB before base64 encoding.")
    st.markdown("---")
    skip_review = st.checkbox("Skip editorial review (faster)", value=False)
    use_cache = st.checkbox("Use current report cache", value=True)
    force_mp3 = st.checkbox("Regenerate MP3 even if exists", value=False)
    new_briefing = st.checkbox("Create a new briefing run", value=False)

if st.session_state.stage is None and use_cache:
    cf = load_json(rpath("json", "_final"))
    if cf:
        st.session_state.final = cf
        st.session_state.review = load_json(rpath("json", "_review"))
        st.session_state.draft = load_json(rpath("json", "_draft"))
        st.session_state.stage = "final"
        st.session_state.mp3_ready = rpath("mp3").exists()
        ds = load_json(delivery_status_path())
        st.session_state.email_sent = bool(ds and ds.get("sent"))
        st.session_state.email_last_error = "" if st.session_state.email_sent else (ds or {}).get("error", "")

# -----------------------------------------------------------------------------
# Header and pipeline
# -----------------------------------------------------------------------------
c1, c2 = st.columns([3, 1])
with c1:
    st.markdown('<div class="pulse-header">📡 TelecomPulse AI</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="pulse-sub">{briefing_type} · {datetime.now().strftime("%A, %B %d, %Y")}</div>', unsafe_allow_html=True)
with c2:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("▶ Generate Briefing", use_container_width=True)
st.markdown("---")

STAGE_MAP = {None: ["pending", "pending", "pending", "pending"], "draft": ["active", "pending", "pending", "pending"], "review": ["done", "active", "pending", "pending"], "approved": ["done", "done", "active", "pending"], "final": ["done", "done", "done", "active"]}
states = STAGE_MAP.get(st.session_state.stage, STAGE_MAP[None])
st.markdown('<div class="pipeline">' + ''.join(f'<div class="stage {s}">{l}</div>' for s, l in zip(states, ["1 · Draft", "2 · Editorial Review", "3 · PDF + Audio", "4 · Delivery"])) + '</div>', unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Pipeline runner
# -----------------------------------------------------------------------------
def email_cfg() -> dict:
    return {"smtp_host": SMTP_HOST, "smtp_port": int(SMTP_PORT), "sender": SENDER_EMAIL, "password": SENDER_APP_PASSWORD, "recipient": RECIPIENT_EMAIL}

if run_btn:
    if new_briefing:
        st.session_state.report_id = run_id(); st.session_state.draft = None; st.session_state.review = None; st.session_state.final = None; st.session_state.mp3_ready = False; st.session_state.email_sent = False; st.session_state.email_last_error = ""; st.session_state.stage = None; use_cache = False; force_mp3 = True
    if not OPENAI_API_KEY:
        st.error("Please set OPENAI_API_KEY in Streamlit secrets or environment variables."); st.stop()
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    with st.status("📝 Stage 1 · Generating draft briefing…", expanded=True) as s1:
        try:
            draft_cache = rpath("json", "_draft")
            if use_cache and draft_cache.exists():
                draft = load_json(draft_cache); st.write("📂 Draft loaded from cache.")
            else:
                st.write(f"🧠 Generating {briefing_type.lower()} across expanded telecom topics…")
                draft = generate_draft(client, model_choice, briefing_type); draft["briefing_type"] = briefing_type; save_json(draft, draft_cache); st.write("✅ Draft generated and saved.")
            st.session_state.draft = draft; st.session_state.stage = "draft"
        except Exception as e:
            st.error(f"Draft generation failed: {e}"); st.stop()
        s1.update(label="✅ Stage 1 · Draft complete", state="complete")

    if skip_review:
        st.info("⏭️ Editorial review skipped. Draft will be used as-is.")
        st.session_state.review = None; st.session_state.final = dict(st.session_state.draft); st.session_state.final["briefing_type"] = briefing_type; save_json(st.session_state.final, rpath("json", "_final")); st.session_state.stage = "approved"
    else:
        with st.status("🔍 Stage 2 · Running internal editorial review…", expanded=True) as s2:
            try:
                review_cache = rpath("json", "_review")
                if use_cache and review_cache.exists():
                    review = load_json(review_cache); st.write("📂 Review loaded from cache.")
                else:
                    st.write("🧐 Chief Editor reviewing clarity, authenticity and professionalism…")
                    review = review_draft(client, model_choice, st.session_state.draft); save_json(review, review_cache); st.write("✅ Review complete.")
                st.session_state.review = review; st.session_state.stage = "review"
                final = build_final(st.session_state.draft, review); final["briefing_type"] = briefing_type; save_json(final, rpath("json", "_final")); st.session_state.final = final; st.session_state.stage = "approved"
                st.write(f"✅ Final report assembled — Score: {review.get('overall_score','?')}/100 · {review.get('overall_verdict','')} · {len(review.get('revised', {}))} section(s) revised internally")
            except Exception as e:
                st.error(f"Editorial review failed: {e}"); st.stop()
            s2.update(label=f"✅ Stage 2 · Review complete — {review.get('overall_score','?')}/100", state="complete")

    final = st.session_state.final
    mp3_out = str(rpath("mp3")); tts_cache = rpath("json", "_tts")
    with st.status("🎙️ Stage 3A · Generating audio script and MP3…", expanded=True) as s3:
        try:
            if force_mp3 or not os.path.exists(mp3_out):
                if use_cache and tts_cache.exists():
                    script = load_json(tts_cache).get("tts_script", ""); st.write("📂 TTS script loaded from cache.")
                else:
                    st.write("🗣️ Generating dedicated spoken briefing script…")
                    script = generate_tts_script(client, model_choice, final, briefing_type)
                    if not script: raise ValueError("OpenAI returned an empty TTS script.")
                    final["tts_script"] = script; st.session_state.final = final; save_json({"tts_script": script}, tts_cache); save_json(final, rpath("json", "_final")); st.write(f"✅ TTS script generated — {len(script.split()):,} words.")
                st.write(f"🔊 Synthesising speech with {voice_label}…")
                generate_mp3(script, voice, mp3_out); st.session_state.mp3_ready = True; st.write(f"✅ MP3 created — {Path(mp3_out).stat().st_size / (1024 * 1024):.1f} MB")
            else:
                st.write("📂 MP3 already exists for this report run."); st.session_state.mp3_ready = True
        except Exception as e:
            st.warning(f"TTS generation error: {e}")
        s3.update(label="✅ Stage 3A · Audio ready", state="complete")

    pdf_out = str(pdf_path())
    with st.status("📄 Stage 3B · Generating professional PDF report…", expanded=True) as spdf:
        try:
            generate_pdf_report(st.session_state.final, pdf_out); st.write(f"✅ PDF report created — {Path(pdf_out).name}")
        except Exception as e:
            st.warning(f"PDF generation error: {e}")
        spdf.update(label="✅ Stage 3B · PDF report ready", state="complete")

    save_email_assets(st.session_state.final, str(email_html_path()), str(email_text_path()), pdf_attached=os.path.exists(pdf_out), mp3_attached=os.path.exists(mp3_out))

    if email_configured_now and auto_email_enabled:
        with st.status("📧 Stage 4 · Sending email delivery…", expanded=True) as s4:
            try:
                result = send_email(email_cfg(), st.session_state.final, mp3_out if st.session_state.mp3_ready else None, pdf_out if os.path.exists(pdf_out) else None, include_mp3=include_mp3_email)
                st.session_state.email_sent = True; st.session_state.email_last_error = ""; st.write(f"✅ Mailed to {RECIPIENT_EMAIL}. PDF attached: {result['pdf_attached']}; MP3 attached: {result['mp3_attached']}.")
                if result.get("mp3_omitted_reason"): st.write(f"ℹ️ {result['mp3_omitted_reason']}")
                s4.update(label="✅ Stage 4 · Delivered", state="complete")
            except Exception as e:
                st.session_state.email_sent = False; st.session_state.email_last_error = str(e); mark_email_failure(e); st.warning(f"Email error: {e}"); s4.update(label="⚠️ Stage 4 · Email failed", state="error")
    elif not email_configured_now:
        st.info("📧 Email skipped — set SMTP/email secrets or environment variables.")
    else:
        st.info("📧 Automatic email disabled. Use the manual email button after generation.")
    st.session_state.stage = "final"
    st.rerun()

# -----------------------------------------------------------------------------
# Display
# -----------------------------------------------------------------------------
final = st.session_state.final
review = st.session_state.review

if final:
    if review:
        score = review.get("overall_score", 0); verdict = review.get("overall_verdict", ""); ed_note = review.get("editorial_summary", "")
        st.markdown(f'<div class="approved-banner">✅ INTERNALLY REVIEWED · <span class="score-badge {score_cls(score)}">{score}/100</span> · {safe_html(verdict)}<br><span style="color:#a7f3d0;font-size:0.83rem;">{safe_html(ed_note)}</span></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="approved-banner" style="background:linear-gradient(135deg,#1a1208,#2d1f00);border-color:#f59e0b;color:#fcd34d;">📋 Report ready · Editorial review was skipped.</div>', unsafe_allow_html=True)

    deals = final.get("key_deals", []) or []
    cols = st.columns(5)
    metrics = [("Brief Type", "Weekly" if str(final.get("briefing_type", "")).startswith("Weekly") else "Daily"), ("Top Items", len(final.get("top_developments", []) or [])), ("Deals", len(deals)), ("Watch Items", len(final.get("what_to_watch_next", []) or [])), ("Run ID", active_report_id())]
    for col, (label, value) in zip(cols, metrics):
        with col: st.markdown(f'<div class="metric-box"><div class="metric-val" style="font-size:1.1rem;">{safe_html(value)}</div><div class="metric-label">{safe_html(label)}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    t_report, t_review, t_audio, t_email_copy = st.tabs(["📋 Final Report", "🔍 Internal Editorial Review", "🎧 Audio, PDF & Delivery", "✉️ Email Copy"])

    with t_report:
        st.markdown(f'<div class="section-card"><div class="section-title">🔭 Executive Summary</div><div class="report-body">{safe_html(final.get("executive_summary", ""))}</div></div>', unsafe_allow_html=True)
        top_devs = final.get("top_developments", []) or []
        if top_devs:
            st.markdown("### 🧭 Top Telecom Developments")
            for item in top_devs:
                if isinstance(item, dict):
                    st.markdown(f'<div class="section-card" style="border-left-color:#00d4ff;padding:0.85rem 1.2rem;margin-bottom:0.6rem;"><strong>{safe_html(item.get("title", ""))}</strong><br><span style="color:#94a3b8;font-size:0.86rem;">{safe_html(item.get("region", ""))} · {safe_html(item.get("domain", ""))}</span><br><span style="color:#cbd5e1;font-size:0.88rem;">{safe_html(item.get("why_it_matters", ""))}</span></div>', unsafe_allow_html=True)
        if final.get("market_signals_vendor_moves"):
            st.markdown(f'<div class="section-card" style="border-left-color:#f59e0b;"><div class="section-title" style="color:#fbbf24;">📈 Market Signals & Vendor Moves</div><div class="report-body">{safe_html(final.get("market_signals_vendor_moves", ""))}</div></div>', unsafe_allow_html=True)
        d_tabs = st.tabs([DOMAIN_LABELS[k][0] for k in DOMAIN_SECTIONS])
        for dtab, key in zip(d_tabs, DOMAIN_SECTIONS):
            with dtab:
                rendered = re.sub(r"##\s+(.+)", r'<div style="font-family:Syne,sans-serif;font-weight:700;font-size:0.85rem;letter-spacing:0.08em;text-transform:uppercase;color:#00d4ff;margin:1.2rem 0 0.4rem;">\1</div>', safe_html(final.get(key, "")))
                st.markdown(f'<div class="report-body">{rendered}</div>', unsafe_allow_html=True)
        if deals:
            st.markdown("### 🔑 Key Deals & Partnerships")
            for deal in deals:
                if isinstance(deal, dict):
                    st.markdown(f'<div class="section-card" style="border-left-color:#7c3aed;padding:0.85rem 1.2rem;margin-bottom:0.5rem;"><span class="tag">{safe_html(str(deal.get("domain", "")).upper())}</span><strong>{safe_html(deal.get("title", ""))}</strong><span style="color:#f59e0b;font-family:monospace;font-size:0.78rem;"> · {safe_html(deal.get("value", ""))}</span><br><span style="color:#94a3b8;font-size:0.85rem;">{safe_html(deal.get("parties", ""))}</span><br><span style="color:#64748b;font-size:0.8rem;font-style:italic;">↳ {safe_html(deal.get("significance", ""))}</span></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="section-card" style="border-left-color:#7c3aed;"><div class="section-title" style="color:#a78bfa;">🧠 Analyst Take</div><div class="report-body">{safe_html(final.get("analyst_take", ""))}</div></div>', unsafe_allow_html=True)
        watch_items = final.get("what_to_watch_next", []) or []
        if watch_items:
            st.markdown("### 👀 What to Watch Next")
            for item in watch_items:
                st.markdown(f"- {item.get('title') if isinstance(item, dict) else item}")
        txt = "\n\n".join([f"TELECOMPULSE {final.get('briefing_type', 'BRIEFING').upper()} — {today_str()}", "=" * 72, CREATOR_FOOTNOTE, "EXECUTIVE SUMMARY", final.get("executive_summary", ""), "TOP DEVELOPMENTS", "\n".join(f"- {i.get('title','')}: {i.get('why_it_matters','')}" for i in top_devs if isinstance(i, dict)), "MARKET SIGNALS", final.get("market_signals_vendor_moves", ""), *sum(([DOMAIN_LABELS[k][0], final.get(k, "")] for k in DOMAIN_SECTIONS), []), "KEY DEALS", "\n".join(f"- {d.get('title','')} ({d.get('domain','')}) — {d.get('parties','')}" for d in deals if isinstance(d, dict)), "ANALYST TAKE", final.get("analyst_take", ""), "WHAT TO WATCH NEXT", "\n".join(f"- {x.get('title') if isinstance(x, dict) else x}" for x in watch_items)])
        st.download_button("⬇️ Download Final Report (TXT)", data=txt, file_name=f"telecom_pulse_{active_report_id()}_final.txt", mime="text/plain")

    with t_review:
        if not review:
            st.info("Editorial review was skipped for this run.")
        else:
            st.markdown(f'<div class="metric-box" style="padding:1.5rem;max-width:260px;"><div class="metric-val" style="font-size:2.6rem;">{review.get("overall_score", 0)}</div><div class="metric-label">Overall / 100</div><br><span class="score-badge {score_cls(review.get("overall_score", 0))}">{safe_html(review.get("overall_verdict", ""))}</span></div>', unsafe_allow_html=True)
            st.markdown(f'<div class="review-card"><div class="report-body" style="font-size:0.9rem;">{safe_html(review.get("editorial_summary", ""))}</div></div>', unsafe_allow_html=True)
            for sec_key, sec_rev in (review.get("reviews", {}) or {}).items():
                if isinstance(sec_rev, dict):
                    with st.expander(f"{sec_key} — {sec_rev.get('score', 0)}/100", expanded=False):
                        st.write("Issues:", sec_rev.get("issues", [])); st.write("Suggestions:", sec_rev.get("suggestions", []))

    with t_audio:
        mp3_file = rpath("mp3")
        if st.session_state.mp3_ready and mp3_file.exists():
            st.markdown('<div class="approved-banner">🎧 Audio briefing ready</div>', unsafe_allow_html=True)
            audio_bytes = mp3_file.read_bytes(); st.audio(audio_bytes, format="audio/mpeg")
            st.markdown(f'<div class="status-line">Voice: {voice_label} · File: {mp3_file.stat().st_size / (1024 * 1024):.1f} MB · {mp3_file.name}</div>', unsafe_allow_html=True)
            st.download_button("⬇️ Download MP3", data=audio_bytes, file_name=f"telecom_pulse_{active_report_id()}.mp3", mime="audio/mpeg")
        else:
            st.info("🎙️ MP3 not yet generated. Click Generate Briefing to create it.")
        st.markdown("---")
        st.markdown("### 📄 PDF Report")
        pdf_file = pdf_path()
        if pdf_file.exists():
            pdf_bytes = pdf_file.read_bytes(); st.success(f"✅ PDF report ready: {pdf_file.name}"); st.download_button("⬇️ Download PDF Report", data=pdf_bytes, file_name=f"telecom_pulse_{active_report_id()}_final_report.pdf", mime="application/pdf")
        else:
            st.info("📄 PDF report not yet generated. Click Generate Briefing to create it.")
        st.markdown("---")
        st.markdown("### 📧 Email Delivery")
        ds = load_json(delivery_status_path()) or {}
        if st.session_state.email_sent or ds.get("sent"):
            st.success(f"✅ Last delivery mailed to **{ds.get('recipient', RECIPIENT_EMAIL)}** at {ds.get('sent_at', 'unknown time')}")
            st.caption(f"PDF attached: {ds.get('pdf_attached')} · MP3 attached: {ds.get('mp3_attached')} · Attachment MB: {ds.get('attachment_mb_before_base64')}")
            if ds.get("mp3_omitted_reason"): st.info(ds.get("mp3_omitted_reason"))
        elif st.session_state.email_last_error or ds.get("error"):
            st.warning(f"Last email attempt failed: {st.session_state.email_last_error or ds.get('error')}")
        elif not email_configured_now:
            st.markdown('<div class="review-issue"><strong>Email not configured.</strong><br>Set SMTP_HOST, SMTP_PORT, SENDER_EMAIL, SENDER_APP_PASSWORD and RECIPIENT_EMAIL in Streamlit secrets or environment variables.</div>', unsafe_allow_html=True)
        else:
            st.info("No delivery has been recorded for this run yet.")

        manual_disabled = not (email_configured_now and final and pdf_file.exists())
        if st.button("📧 Send Email Now", use_container_width=True, disabled=manual_disabled):
            try:
                result = send_email(email_cfg(), final, str(mp3_file) if mp3_file.exists() else None, str(pdf_file) if pdf_file.exists() else None, include_mp3=include_mp3_email)
                st.session_state.email_sent = True; st.session_state.email_last_error = ""; st.success(f"Email sent to {RECIPIENT_EMAIL}. PDF attached: {result['pdf_attached']}; MP3 attached: {result['mp3_attached']}.")
                if result.get("mp3_omitted_reason"): st.info(result["mp3_omitted_reason"])
            except Exception as e:
                st.session_state.email_sent = False; st.session_state.email_last_error = str(e); mark_email_failure(e); st.error(f"Manual email failed: {e}")

    with t_email_copy:
        st.markdown("### ✉️ Professional Email Copy")
        html_file = email_html_path(); text_file = email_text_path()
        if not html_file.exists() or not text_file.exists():
            try: save_email_assets(final, str(html_file), str(text_file), pdf_attached=pdf_path().exists(), mp3_attached=rpath("mp3").exists())
            except Exception as e: st.warning(f"Could not prepare email copy: {e}")
        st.markdown("**Subject line**"); st.code(email_subject(final), language="text")
        if text_file.exists(): st.text_area("Plain-text email", text_file.read_text(encoding="utf-8"), height=360, label_visibility="collapsed")
        c_html, c_txt = st.columns(2)
        if html_file.exists():
            with c_html: st.download_button("⬇️ Download Branded Email HTML", data=html_file.read_bytes(), file_name=f"telecom_pulse_{active_report_id()}_email_brief.html", mime="text/html", use_container_width=True)
        if text_file.exists():
            with c_txt: st.download_button("⬇️ Download Plain-Text Email", data=text_file.read_bytes(), file_name=f"telecom_pulse_{active_report_id()}_email_brief.txt", mime="text/plain", use_container_width=True)
        if html_file.exists(): st.components.v1.html(html_file.read_text(encoding="utf-8"), height=780, scrolling=True)
else:
    st.markdown("""
    <div style="text-align:center;padding:4rem 2rem;"><div style="font-size:4.5rem;">📡</div><div style="font-family:'Syne',sans-serif;font-size:1.5rem;color:#64748b;margin-top:1rem;">Set secrets/environment variables and click <strong style="color:#00d4ff;">▶ Generate Briefing</strong></div><div style="font-family:'Space Mono',monospace;font-size:0.7rem;color:#1e3a5f;margin-top:1.2rem;letter-spacing:0.15em;">WEEKLY INTELLIGENCE → INTERNAL REVIEW → PDF + AUDIO → EMAIL</div></div>
    """, unsafe_allow_html=True)

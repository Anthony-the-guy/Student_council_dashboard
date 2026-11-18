import re
import math
import time
import hmac
import html
import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st
import pandas as pd

st.set_page_config(page_title="Student Council Finance Dashboard", page_icon="🏛️", layout="wide")

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
CSV_PATH = Path(__file__).parent / "logIn_info.csv"   # keep the CSVs and logo next to this file
STATEMENT_PATH = Path(__file__).parent / "Student_council_bank_statements.csv"
LOGO_PATH = Path(__file__).parent / "psc_logo.png"
MAX_ATTEMPTS = 5          # wrong tries before a short lock
LOCK_SECONDS = 30         # how long the lock lasts
EMAIL_PATTERN = re.compile(
    r"^[a-z]+(?:[-'][a-z]+)*\.[a-z]+(?:[-'][a-z]+)*@pau\.edu\.ng$", re.IGNORECASE
)  # first.surname@pau.edu.ng


# ----------------------------------------------------------------------------
# Data + login logic
# ----------------------------------------------------------------------------
@st.cache_data
def load_users(path: str) -> pd.DataFrame:
    """Read the credentials file. 'utf-8-sig' strips the invisible BOM that
    Excel adds to the first column name."""
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = [c.strip().lower() for c in df.columns]      # Email -> email
    df["email"] = df["email"].str.strip().str.lower()
    df["password"] = df["password"].str.strip()
    return df


def is_valid_pau_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


def check_credentials(email: str, password: str) -> bool:
    users = load_users(str(CSV_PATH))
    match = users[users["email"] == email.strip().lower()]
    if match.empty:
        return False
    # compare_digest avoids leaking info through comparison timing
    return hmac.compare_digest(match.iloc[0]["password"], password)


def name_from_email(email: str) -> str:
    first, surname = email.split("@")[0].split(".", 1)
    return f"{first.title()} {surname.title()}"



# ----------------------------------------------------------------------------
# Bank statement data + formatting
# ----------------------------------------------------------------------------
def _to_amount(value) -> float:
    """DR/CR cells are messy: ' 10,000 ', '-', '' and '15000' all appear."""
    s = str(value).replace(",", "").replace("₦", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


@st.cache_data
def load_statement(path: str) -> pd.DataFrame:
    """Read the bank statement and keep its original columns:
    Date | Transaction type | Heading | Description | DR | CR
    plus a running Balance."""
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    df.columns = [c.strip() for c in df.columns]
    for col in ("Transaction type", "Heading", "Description"):
        df[col] = df[col].str.strip()
    df["Date"] = pd.to_datetime(df["Date"].str.strip(), format="%d-%b-%y", errors="coerce")
    df["DR"] = df["DR"].map(_to_amount)
    df["CR"] = df["CR"].map(_to_amount)
    df = df.dropna(subset=["Date"]).sort_values("Date", kind="stable").reset_index(drop=True)
    df["Balance"] = (df["CR"] - df["DR"]).cumsum()
    return df


def naira(x: float) -> str:
    return f"-₦{abs(x):,.0f}" if x < 0 else f"₦{x:,.0f}"


def compact(x: float) -> str:
    """₦21.8M, ₦450K, ₦0 - used on chart axes."""
    a = abs(x)
    if a >= 1e9:
        s = f"{a / 1e9:.1f}".rstrip("0").rstrip(".") + "B"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    elif a >= 1e3:
        s = f"{a / 1e3:.0f}K"
    else:
        s = f"{a:.0f}"
    return ("-" if x < 0 else "") + "₦" + s


def day_label(d) -> str:
    return f"{d.day} {d:%b}"


def esc(x) -> str:
    return html.escape(str(x))


def squash(markup: str) -> str:
    """Collapse whitespace so Markdown never mistakes indented HTML for a code block."""
    return " ".join(markup.split())


def render(markup: str) -> None:
    st.markdown(squash(markup), unsafe_allow_html=True)


@lru_cache(maxsize=1)
def _logo_b64() -> str:
    try:
        return base64.b64encode(LOGO_PATH.read_bytes()).decode()
    except OSError:
        return ""


def logo_img(size: int) -> str:
    data = _logo_b64()
    if not data:
        return '<span class="brand-dot"></span>'
    return (
        f'<img class="logo" width="{size}" height="{size}" '
        f'src="data:image/png;base64,{data}" alt="Pan-Atlantic University Student Council logo">'
    )


# ----------------------------------------------------------------------------
# Dashboard building blocks (each returns an HTML string)
# ----------------------------------------------------------------------------
IN_COLOR = "#138A5E"
OUT_COLOR = "#C8452D"


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def balance_chart(df: pd.DataFrame) -> str:
    """Step chart of the running balance, one point per day with activity."""
    daily = df.groupby("Date").agg(cr=("CR", "sum"), dr=("DR", "sum")).sort_index()
    daily["net"] = daily["cr"] - daily["dr"]
    daily["bal"] = daily["net"].cumsum()

    lo, hi = min(0.0, daily["bal"].min()), max(0.0, daily["bal"].max())
    step = _nice_step((hi - lo) / 4 if hi > lo else 1)
    y_min, y_max = math.floor(lo / step) * step, math.ceil(hi / step) * step
    if y_max == y_min:
        y_max = y_min + step

    W, H, L, R, T, B = 760, 290, 62, 18, 14, 34
    t0, t1 = daily.index[0], daily.index[-1]
    span = max((t1 - t0).days, 1)

    def X(d):
        return L + (d - t0).days / span * (W - L - R)

    def Y(v):
        return T + (1 - (v - y_min) / (y_max - y_min)) * (H - T - B)

    parts = []
    n_ticks = int(round((y_max - y_min) / step))
    for i in range(n_ticks + 1):
        v = y_min + i * step
        parts.append(f'<line class="gl" x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        parts.append(f'<text class="ax" x="{L - 10}" y="{Y(v) + 4:.1f}" text-anchor="end">{compact(v)}</text>')

    for i in range(5):
        d = t0 + (t1 - t0) * i / 4
        anchor = "start" if i == 0 else "end" if i == 4 else "middle"
        parts.append(f'<text class="ax" x="{X(d):.1f}" y="{H - 10}" text-anchor="{anchor}">{day_label(d)}</text>')

    dates = list(daily.index)
    bals = list(daily["bal"])
    path = f"M{X(dates[0]):.1f},{Y(0):.1f} V{Y(bals[0]):.1f}"
    for d, b in zip(dates[1:], bals[1:]):
        path += f" H{X(d):.1f} V{Y(b):.1f}"
    area = f"{path} V{Y(0):.1f} Z"
    parts.append(f'<path d="{area}" fill="#1F6FEB" fill-opacity="0.09"/>')
    parts.append(f'<path d="{path}" fill="none" stroke="#1F6FEB" stroke-width="2.5" stroke-linejoin="round"/>')

    for d, row in daily.iterrows():
        colour = IN_COLOR if row["net"] >= 0 else OUT_COLOR
        tip = f"{d:%d %b %Y}: in {naira(row['cr'])}, out {naira(row['dr'])}, balance {naira(row['bal'])}"
        parts.append(
            f'<circle cx="{X(d):.1f}" cy="{Y(row["bal"]):.1f}" r="5" fill="{colour}" stroke="#fff" stroke-width="2">'
            f"<title>{esc(tip)}</title></circle>"
        )

    return (
        '<div class="card"><p class="card-title">Cash balance over time</p>'
        '<p class="card-sub">Running balance after each day with activity. '
        'Green dots are net inflow days, red dots are net outflow days.</p>'
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
        'aria-label="Line chart of the Student Council running cash balance">' + "".join(parts) + "</svg></div>"
    )


def heading_card(df: pd.DataFrame) -> str:
    """Every heading in the statement with its total, bar scaled to the largest."""
    rows = []
    for (ttype, heading), g in df.groupby(["Transaction type", "Heading"]):
        is_in = g["CR"].sum() >= g["DR"].sum()
        rows.append((heading, ttype, g["CR"].sum() if is_in else g["DR"].sum(), is_in))
    rows.sort(key=lambda r: -r[2])
    return '<div class="card"><p class="card-title">By heading</p><p class="card-sub">What came in and what went out</p>' + hbars(
        [(h, t, v, IN_COLOR if i else OUT_COLOR) for h, t, v, i in rows]
    ) + "</div>"


def hbars(items) -> str:
    """items: (name, small-caption, value, colour)"""
    if not items:
        return '<p class="card-sub">Nothing to show yet.</p>'
    top = max(v for _, _, v, _ in items) or 1
    out = []
    for name, cap, val, colour in items:
        width = max(val / top * 100, 1.2) if val > 0 else 0
        small = f"<small>{esc(cap)}</small>" if cap else ""
        out.append(
            f'<div class="hrow"><div class="hname">{esc(name)}{small}</div>'
            f'<div class="htrack"><div class="hfill" style="width:{width:.1f}%;background:{colour}"></div></div>'
            f'<div class="hval">{naira(val)}</div></div>'
        )
    return "".join(out)


def monthly_table(df: pd.DataFrame) -> str:
    m = df.assign(Month=df["Date"].dt.to_period("M")).groupby("Month").agg(cr=("CR", "sum"), dr=("DR", "sum"))
    body = ""
    for month, r in m.iterrows():
        net = r["cr"] - r["dr"]
        body += (
            f"<tr><td>{month.strftime('%B %Y')}</td><td>{naira(r['cr'])}</td><td>{naira(r['dr'])}</td>"
            f'<td class="{"pos" if net >= 0 else "neg"}">{naira(net)}</td></tr>'
        )
    net = m["cr"].sum() - m["dr"].sum()
    body += (
        f'<tr class="tot"><td>Total</td><td>{naira(m["cr"].sum())}</td><td>{naira(m["dr"].sum())}</td>'
        f'<td class="{"pos" if net >= 0 else "neg"}">{naira(net)}</td></tr>'
    )
    return (
        '<div class="card"><p class="card-title">Month by month</p><p class="card-sub">Inflow, outflow and net for each month</p>'
        '<div class="scroll-x"><table class="tbl"><thead><tr><th>Month</th><th>Inflow</th><th>Outflow</th><th>Net</th></tr></thead>'
        f"<tbody>{body}</tbody></table></div></div>"
    )


def chips(frame: pd.DataFrame, field: str, colour: str, noun: str) -> str:
    """One summary tile per heading. field is 'CR' or 'DR'."""
    total = frame[field].sum() or 1
    tiles = []
    for heading, g in sorted(frame.groupby("Heading"), key=lambda kv: -kv[1][field].sum()):
        amt = g[field].sum()
        n = len(g)
        uniq = g.loc[g[field] > 0, field].unique()
        each = f" of {naira(uniq[0])} each" if len(uniq) == 1 and n > 1 else ""
        tiles.append(
            f'<div class="chip" style="border-left-color:{colour}">'
            f'<div class="chip-label">{esc(heading)}</div><div class="chip-val">{naira(amt)}</div>'
            f'<div class="chip-sub">{n:,} {noun}{each}</div>'
            f'<div class="chip-sub">{amt / total:.1%} of total</div></div>'
        )
    return '<div class="chips">' + "".join(tiles) + "</div>"


def ledger(frame: pd.DataFrame, key: str, balance: bool = False) -> None:
    """Show transactions in the same layout as the bank statement."""
    cols = ["Date", "Transaction type", "Heading", "Description", "DR", "CR"] + (["Balance"] if balance else [])
    view = frame[cols]
    money = lambda v: "–" if v == 0 else f"{v:,.0f}"
    fmt = {"Date": lambda d: f"{d:%B %d, %Y}", "DR": money, "CR": money}
    if balance:
        fmt["Balance"] = lambda v: f"{v:,.0f}"
    styled = (
        view.style.format(fmt)
        .set_properties(subset=["DR"], **{"color": OUT_COLOR})
        .set_properties(subset=["CR"], **{"color": IN_COLOR})
    )
    st.dataframe(styled, hide_index=True, height=440)
    export = view.copy()
    export["Date"] = export["Date"].dt.strftime("%B %d, %Y")
    st.download_button(
        "Download this view (CSV)", export.to_csv(index=False).encode("utf-8-sig"),
        file_name="student_council_statement_view.csv", mime="text/csv", key=f"dl_{key}",
    )

# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
st.session_state.setdefault("user", None)
st.session_state.setdefault("failed_attempts", 0)
st.session_state.setdefault("locked_until", 0.0)


# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=Figtree:wght@400;500;600&display=swap');

:root {
  --ink: #0E2238;
  --ink-soft: #35506B;
  --paper: #F3F5F8;
  --card: #FFFFFF;
  --line: #D5DCE5;
  --accent: #1F6FEB;
  --accent-dark: #1757BC;
  --signal: #F2B544;
  --error-bg: #FDECEC;
  --error-text: #A32121;
  --in: #138A5E;
  --out: #C8452D;
}

html, body, [class*="css"], .stApp { font-family: 'Figtree', sans-serif; }
.stApp { background: var(--paper); }

/* hide Streamlit chrome so the page feels like its own app */
[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer { display: none; }
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
.block-container { max-width: 1060px; padding-top: 4vh; padding-bottom: 4vh; }

/* ---------- left brand panel ---------- */
.brand-panel {
  background: var(--ink);
  color: #fff;
  border-radius: 20px;
  padding: 44px 40px;
  min-height: 560px;
  display: flex; flex-direction: column; justify-content: space-between;
  position: relative; overflow: hidden;
}
.brand-panel h1 {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-weight: 700; font-size: 2.7rem; line-height: 1.05;
  letter-spacing: -0.02em; color: #fff; margin: 0 0 14px 0; padding: 0;
}
.brand-panel p { color: #B9C8D9; font-size: 1.02rem; line-height: 1.55; max-width: 30ch; margin: 0; }
.brand-name { font-weight: 600; font-size: 0.95rem; color: #fff; display: flex; align-items: center; gap: 12px; }
.brand-name .logo { border-radius: 50%; box-shadow: 0 0 0 2px rgba(255,255,255,.25); }
.brand-dot { width: 12px; height: 12px; border-radius: 3px; background: var(--signal); display: inline-block; }

/* the one memorable element: a bar chart that grows in on load */
.bars { display: flex; align-items: flex-end; gap: 12px; height: 150px; margin-top: 28px; }
.bars span {
  flex: 1; border-radius: 6px 6px 0 0; background: #24456B;
  transform-origin: bottom; animation: rise 0.9s cubic-bezier(.2,.8,.2,1) both;
}
.bars span:nth-child(1) { height: 38%; animation-delay: .05s; }
.bars span:nth-child(2) { height: 55%; animation-delay: .15s; }
.bars span:nth-child(3) { height: 46%; animation-delay: .25s; }
.bars span:nth-child(4) { height: 72%; animation-delay: .35s; }
.bars span:nth-child(5) { height: 64%; animation-delay: .45s; }
.bars span:nth-child(6) { height: 100%; animation-delay: .55s; background: var(--signal); }
@keyframes rise { from { transform: scaleY(0); } to { transform: scaleY(1); } }
@media (prefers-reduced-motion: reduce) { .bars span { animation: none; } }

/* ---------- right form side ---------- */
.form-heading {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-weight: 700; font-size: 2rem; color: var(--ink);
  letter-spacing: -0.015em; margin: 0 0 6px 0;
}
.form-sub { color: var(--ink-soft); margin: 0 0 22px 0; font-size: 1rem; }

[data-testid="stForm"] {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 16px; padding: 26px 26px 18px 26px;
  box-shadow: 0 10px 30px rgba(14, 34, 56, 0.06);
}
[data-testid="stForm"] label p { color: var(--ink); font-weight: 600; font-size: 0.92rem; }
[data-testid="stForm"] input {
  border-radius: 10px; color: var(--ink); font-size: 1rem;
}
[data-baseweb="input"], [data-baseweb="base-input"] { border-radius: 10px; }
[data-baseweb="input"]:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 3px rgba(31,111,235,.18); }

[data-testid="stFormSubmitButton"] button {
  width: 100%; background: var(--accent); color: #fff; border: none;
  border-radius: 10px; padding: 0.7rem 1rem; font-weight: 600; font-size: 1rem;
  transition: background .15s ease;
}
[data-testid="stFormSubmitButton"] button:hover { background: var(--accent-dark); color: #fff; }
[data-testid="stFormSubmitButton"] button:focus-visible { outline: 3px solid rgba(31,111,235,.4); outline-offset: 2px; }

.msg-error {
  background: var(--error-bg); color: var(--error-text);
  border-radius: 10px; padding: 12px 14px; margin-top: 14px; font-size: 0.93rem;
}
.hint { color: var(--ink-soft); font-size: 0.85rem; margin-top: 16px; }

/* ---------- after login ---------- */
.topbar { display: flex; align-items: center; gap: 16px; }
.topbar .logo { border-radius: 50%; box-shadow: 0 0 0 3px #fff, 0 0 0 4px var(--line); flex: none; }
.topbar .t1 { font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; font-size: 1.55rem; color: var(--ink); line-height: 1.1; margin: 0; letter-spacing: -0.01em; }
.topbar .t2 { color: var(--ink-soft); font-size: 0.92rem; margin: 3px 0 0 0; }

/* hero: the one memorable block */
.hero {
  background: var(--ink); color: #fff; border-radius: 20px; padding: 34px 38px;
  display: grid; grid-template-columns: 1.5fr 1fr; gap: 36px; margin: 20px 0 24px 0;
}
.hero-label { color: #B9C8D9; font-size: 0.95rem; font-weight: 500; }
.hero-value {
  font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; letter-spacing: -0.025em;
  font-size: clamp(2.3rem, 5.2vw, 3.7rem); line-height: 1.05; margin: 6px 0 4px 0;
  font-variant-numeric: tabular-nums;
}
.hero-note { color: #8FA5BC; font-size: 0.9rem; }
.flow {
  display: flex; height: 14px; border-radius: 7px; overflow: hidden; margin: 28px 0 10px 0;
  background: #24456B; transform-origin: left; animation: grow 1.1s cubic-bezier(.2,.8,.2,1) both;
}
.flow-in { background: #3FCF9B; } .flow-out { background: #F0705A; }
.flow-cap { color: #B9C8D9; font-size: 0.92rem; }
@keyframes grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@media (prefers-reduced-motion: reduce) { .flow { animation: none; } }
.hero-side { display: flex; flex-direction: column; justify-content: center; border-left: 1px solid #24456B; padding-left: 36px; }
.side-row { padding: 16px 0; } .side-row + .side-row { border-top: 1px solid #24456B; }
.side-label { display: flex; align-items: center; gap: 8px; color: #B9C8D9; font-size: 0.92rem; }
.sq { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.side-val { font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; font-size: 1.75rem; margin-top: 4px; font-variant-numeric: tabular-nums; }
.side-sub { color: #8FA5BC; font-size: 0.85rem; margin-top: 2px; }

/* tabs */
[data-baseweb="tab-list"] { gap: 6px; }
button[data-baseweb="tab"] { background: transparent; padding: 10px 18px; }
button[data-baseweb="tab"] p { font-weight: 600; font-size: 1rem; color: var(--ink-soft); }
button[data-baseweb="tab"][aria-selected="true"] p { color: var(--ink); }
[data-baseweb="tab-highlight"] { background-color: var(--signal) !important; height: 3px !important; }
[data-baseweb="tab-border"] { background-color: var(--line) !important; }

/* cards, chips, bars, tables */
.card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 22px 24px; margin-bottom: 16px; }
.card-title { font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; font-size: 1.15rem; color: var(--ink); margin: 0; }
.card-sub { color: var(--ink-soft); font-size: 0.88rem; margin: 2px 0 14px 0; }
.chart { width: 100%; height: auto; display: block; }
.chart .gl { stroke: #E4E9F0; stroke-width: 1; }
.chart .ax { fill: #5C7389; font-size: 12px; font-family: 'Figtree', sans-serif; }
.chips { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin: 6px 0 18px 0; }
.chip { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 12px; padding: 16px 18px; }
.chip-label { color: var(--ink-soft); font-size: 0.9rem; font-weight: 600; }
.chip-val { font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; font-size: 1.55rem; color: var(--ink); margin: 2px 0 4px 0; font-variant-numeric: tabular-nums; }
.chip-sub { color: var(--ink-soft); font-size: 0.82rem; }
.hrow { display: grid; grid-template-columns: minmax(110px, 1.2fr) 2.4fr auto; gap: 14px; align-items: center; padding: 10px 0; border-top: 1px solid #EDF1F6; }
.hrow:first-of-type { border-top: none; }
.hname { color: var(--ink); font-weight: 600; font-size: 0.93rem; }
.hname small { display: block; color: var(--ink-soft); font-weight: 400; font-size: 0.78rem; }
.htrack { background: #EDF1F6; border-radius: 6px; height: 10px; overflow: hidden; }
.hfill { height: 100%; border-radius: 6px; }
.hval { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--ink); font-size: 0.93rem; text-align: right; min-width: 100px; }
.scroll-x { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 0.93rem; }
.tbl th { text-align: right; color: var(--ink-soft); font-weight: 600; font-size: 0.85rem; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.tbl td { padding: 12px 10px; border-bottom: 1px solid #EDF1F6; text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
.tbl th:first-child, .tbl td:first-child { text-align: left; }
.tbl tr:last-child td { border-bottom: none; }
.tbl tr.tot td { font-weight: 700; border-top: 1px solid var(--line); }
.pos { color: var(--in) !important; } .neg { color: var(--out) !important; }
.section { font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; font-size: 1.15rem; color: var(--ink); margin: 14px 0 6px 0; }
.meta { color: var(--ink-soft); font-size: 0.88rem; margin: 2px 0 10px 0; }
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
[data-testid="stDownloadButton"] button { border-radius: 10px; border: 1px solid var(--line); color: var(--ink); font-weight: 600; }

@media (max-width: 800px) {
  .brand-panel { min-height: 320px; padding: 30px 26px; }
  .brand-panel h1 { font-size: 2.1rem; }
  .bars { height: 90px; }
  .hero { grid-template-columns: 1fr; padding: 26px 22px; gap: 22px; }
  .hero-side { border-left: none; border-top: 1px solid #24456B; padding-left: 0; padding-top: 6px; }
  .hrow { grid-template-columns: 1fr; gap: 6px; }
  .hval { text-align: left; }
  .topbar .t1 { font-size: 1.25rem; }
}
</style>
""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Views
# ----------------------------------------------------------------------------
def show_login():
    left, right = st.columns([1.05, 1], gap="large")

    with left:
        st.markdown(
            f"""
<div class="brand-panel">
<div class="brand-name">{logo_img(44)}<span>Pan-Atlantic University Student Council</span></div>
<div>
<h1>Every naira the Council handles, in one view.</h1>
<p>Log in with your PAU email to open the finance dashboard.</p>
<div class="bars"><span></span><span></span><span></span><span></span><span></span><span></span></div>
</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with right:
        st.markdown('<div style="height:6vh"></div>', unsafe_allow_html=True)
        st.markdown('<h2 class="form-heading">Log in</h2>', unsafe_allow_html=True)
        st.markdown('<p class="form-sub">Use your PAU email and password.</p>', unsafe_allow_html=True)

        with st.form("login_form", clear_on_submit=False):
            email = st.text_input("PAU email", placeholder="first.surname@pau.edu.ng")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Log in")

        error = None
        if submitted:
            now = time.time()
            if now < st.session_state.locked_until:
                wait = int(st.session_state.locked_until - now) + 1
                error = f"Too many failed attempts. Try again in {wait} seconds."
            elif not email.strip() or not password:
                error = "Enter both your email and password."
            elif not is_valid_pau_email(email.strip()):
                error = "Use your PAU email, written as first.surname@pau.edu.ng."
            elif check_credentials(email, password):
                st.session_state.user = {
                    "email": email.strip().lower(),
                    "name": name_from_email(email.strip()),
                }
                st.session_state.failed_attempts = 0
                st.rerun()
            else:
                st.session_state.failed_attempts += 1
                left_over = MAX_ATTEMPTS - st.session_state.failed_attempts
                if left_over <= 0:
                    st.session_state.locked_until = now + LOCK_SECONDS
                    st.session_state.failed_attempts = 0
                    error = f"Too many failed attempts. Try again in {LOCK_SECONDS} seconds."
                else:
                    error = f"Email or password is incorrect. {left_over} attempt(s) left."

        if error:
            st.markdown(f'<div class="msg-error" role="alert">{html.escape(error)}</div>', unsafe_allow_html=True)

        st.markdown('<p class="hint">Can\'t log in? Ask the Council admin to check your details.</p>', unsafe_allow_html=True)


def show_dashboard():
    user = st.session_state.user
    first = user["name"].split()[0]

    top, out = st.columns([6, 1])
    with top:
        render(f"""
<div class="topbar">{logo_img(56)}
<div><p class="t1">Student Council Finance</p>
<p class="t2">Welcome, {esc(first)} &nbsp;|&nbsp; {esc(user["email"])}</p></div></div>""")
    with out:
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        if st.button("Log out", use_container_width=True):
            st.session_state.user = None
            st.rerun()

    if not STATEMENT_PATH.exists():
        st.error(f"Could not find {STATEMENT_PATH.name}. Keep it in the same folder as this file.")
        return
    df = load_statement(str(STATEMENT_PATH))
    if df.empty:
        st.warning("The bank statement has no readable transactions.")
        return

    # ---- headline numbers: money in, money out, net ----
    inflow, outflow = df["CR"].sum(), df["DR"].sum()
    net = inflow - outflow
    n_in, n_out = int((df["CR"] > 0).sum()), int((df["DR"] > 0).sum())
    d0, d1 = df["Date"].min(), df["Date"].max()
    out_pct = min(outflow / inflow * 100, 100) if inflow > 0 else 100.0
    label = "Net inflow from operations" if net >= 0 else "Net outflow from operations"
    caption = (
        f"₦{out_pct:.2f} of every ₦100 received has gone out." if inflow > 0 else "No money has come in yet."
    )
    render(f"""
<div class="hero">
<div>
<div class="hero-label">{label}</div>
<div class="hero-value">{naira(net)}</div>
<div class="hero-note">Money in less money out, {day_label(d0)} to {day_label(d1)} {d1.year}</div>
<div class="flow" role="img" aria-label="{caption}"><span class="flow-in" style="width:{100 - out_pct:.2f}%"></span><span class="flow-out" style="width:{out_pct:.2f}%"></span></div>
<div class="flow-cap">{caption}</div>
</div>
<div class="hero-side">
<div class="side-row"><div class="side-label"><span class="sq" style="background:#3FCF9B"></span>Total inflow</div>
<div class="side-val">{naira(inflow)}</div><div class="side-sub">{n_in:,} receipts</div></div>
<div class="side-row"><div class="side-label"><span class="sq" style="background:#F0705A"></span>Total outflow</div>
<div class="side-val">{naira(outflow)}</div><div class="side-sub">{n_out:,} payments</div></div>
</div>
</div>""")

    t_over, t_in, t_out, t_stmt = st.tabs(["Overview", "Income", "Expenses", "Statement"])

    # ---- Overview ----
    with t_over:
        left, right = st.columns([1.6, 1], gap="medium")
        with left:
            render(balance_chart(df))
        with right:
            render(heading_card(df))
        render(monthly_table(df))

    # ---- Income ----
    with t_in:
        inc = df[df["Transaction type"] == "Income"]
        render(chips(inc, "CR", IN_COLOR, "receipts"))
        heads = ["All"] + sorted(inc["Heading"].unique())
        pick = st.radio("Heading", heads, horizontal=True, key="inc_head", label_visibility="collapsed")
        shown = inc if pick == "All" else inc[inc["Heading"] == pick]
        render(f'<p class="section">Income transactions</p><p class="meta">{len(shown):,} transactions, {naira(shown["CR"].sum())} received</p>')
        ledger(shown, "income")

    # ---- Expenses ----
    with t_out:
        exp = df[df["Transaction type"] == "Expenses"]
        if exp.empty:
            st.info("No expenses recorded yet.")
        else:
            render(chips(exp, "DR", OUT_COLOR, "payments"))
            top_items = exp.sort_values("DR", ascending=False)
            render(
                '<div class="card"><p class="card-title">Largest payments</p><p class="card-sub">Every expense, biggest first</p>'
                + hbars([(r["Description"] or r["Heading"], r["Heading"], r["DR"], OUT_COLOR) for _, r in top_items.iterrows()])
                + "</div>"
            )
            heads = ["All"] + sorted(exp["Heading"].unique())
            pick = st.radio("Heading", heads, horizontal=True, key="exp_head", label_visibility="collapsed")
            shown = exp if pick == "All" else exp[exp["Heading"] == pick]
            render(f'<p class="section">Expense transactions</p><p class="meta">{len(shown):,} transactions, {naira(shown["DR"].sum())} spent</p>')
            ledger(shown, "expenses")

    # ---- Statement (full bank statement layout) ----
    with t_stmt:
        f1, f2, f3 = st.columns([1, 1, 1.4])
        with f1:
            ttype = st.selectbox("Transaction type", ["All"] + sorted(df["Transaction type"].unique()), key="st_type")
        pool = df if ttype == "All" else df[df["Transaction type"] == ttype]
        with f2:
            head = st.selectbox("Heading", ["All"] + sorted(pool["Heading"].unique()), key="st_head")
        with f3:
            query = st.text_input("Search description", placeholder="e.g. DJ, refreshments, Fromica", key="st_q")
        view = pool if head == "All" else pool[pool["Heading"] == head]
        if query.strip():
            view = view[view["Description"].str.contains(query.strip(), case=False, regex=False, na=False)]
        render(
            f'<p class="meta">Showing {len(view):,} of {len(df):,} transactions. '
            f'DR (money out) {naira(view["DR"].sum())} &nbsp;|&nbsp; CR (money in) {naira(view["CR"].sum())}. '
            "Balance is the running account balance.</p>"
        )
        ledger(view, "statement", balance=True)



# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------
if st.session_state.user:
    show_dashboard()
else:
    show_login()

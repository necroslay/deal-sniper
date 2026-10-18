"""Streamlit viewer for scraped marketplace listings.

    python -m app.scrape              # fill the database
    streamlit run app/dashboard.py    # browse it

Shows every listing with filters for source, product, price and recency, plus a
toggle to hide listings the guard flagged as accessories or lots.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.db.store import DB, Store
from app.engines import logistics
from app.engines.laptops import parse_laptop

# Allow pointing the viewer at a different database (handy for tests, or for
# keeping several scraped datasets side by side).
DB_PATH = os.environ.get("DEAL_SNIPER_DB", DB)

st.set_page_config(page_title="Deal Sniper", layout="wide")


@st.cache_data(ttl=30, show_spinner=False)
def load_listings(db_path: str) -> pd.DataFrame:
    """Load every stored listing into a DataFrame (briefly cached)."""
    store = Store(db=db_path).connect()
    try:
        rows = store.all_listings()
    finally:
        store.close()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    for col in ("date_posted", "first_seen_at", "last_seen_at"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    df["price_cad"] = pd.to_numeric(df["price_cad"], errors="coerce")
    df["flagged"] = df["flag_reason"].fillna("").astype(str).str.len() > 0
    df["product_key"] = df["product_key"].fillna("unresolved")

    # Can it be mailed, and what does it cost to land? (estimates; see
    # engines/logistics.py and CFG.logistics)
    def _logistics(row):
        category = "laptop" if parse_laptop(row["title"]) else "gpu"
        info = logistics.assess(row.to_dict(), category)
        return pd.Series({"shipping": info.status,
                          "landed": row["price_cad"] + info.shipping_cad})

    df[["shipping", "landed"]] = df.apply(_logistics, axis=1)
    return df


def run_scrape(source: str, pages: int | None, db_path: str) -> tuple[int, str]:
    """Run the scraper as a subprocess, returning ``(exit_code, output)``.

    A subprocess rather than an in-process call, for two reasons: the Craigslist
    adapter drives Playwright's *sync* API (which dislikes sharing a process with
    Streamlit), and a scraper crash can't take the viewer down with it.

    The same ``app.scrape`` CLI the terminal uses is invoked, so there is exactly
    one code path to keep working.
    """
    cmd = [sys.executable, "-m", "app.scrape", "--source", source, "--db", db_path]
    if pages:
        cmd += ["--pages", str(pages)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(REPO_ROOT), timeout=900)
    except subprocess.TimeoutExpired:
        return 1, "Scrape timed out after 15 minutes."
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


st.title("💰 Deal Sniper")
st.caption("Scraped second-hand computer parts — Kijiji (Canada-wide) + "
           "Craigslist (GTA)")

df = load_listings(DB_PATH)

# ── Run a scrape ───────────────────────────────────────────────────────────
# Rendered before the empty-database guard on purpose: a fresh install should be
# able to populate its own database from here, not just display one.
st.sidebar.header("Run a scrape")
scrape_source = st.sidebar.selectbox("Source", ["all", "kijiji", "craigslist"],
                                     key="scrape_source")
scrape_pages = st.sidebar.number_input("Pages (0 = use config)", min_value=0,
                                       max_value=20, value=0, key="scrape_pages")

if st.sidebar.button("▶  Run scrape now", width="stretch",
                     type="primary", key="run_scrape"):
    with st.spinner(f"Scraping {scrape_source}… this can take a minute"):
        code, output = run_scrape(scrape_source, scrape_pages or None, DB_PATH)
    load_listings.clear()                       # drop the cached view
    st.session_state["scrape_result"] = {"code": code, "output": output}
    st.rerun()

# The result is stashed in session state so it survives the rerun above.
if "scrape_result" in st.session_state:
    result = st.session_state["scrape_result"]
    del st.session_state["scrape_result"]
    if result["code"] == 0:
        st.success("Scrape finished — the figures below are refreshed.")
    else:
        st.warning("Scrape finished with problems.")
    with st.expander("Scrape output", expanded=True):
        st.code(result["output"] or "(no output)", language="text")

st.sidebar.divider()

if df.empty:
    st.info(
        "No listings yet. Use **▶ Run scrape now** in the sidebar, or run the "
        "CLI:\n\n```\npython -m app.scrape\n```"
    )
    st.stop()

# ── Header metrics ──────────────────────────────────────────────────────────
now = pd.Timestamp.now(tz="UTC")
new_24h = int((df["first_seen_at"] >= now - pd.Timedelta(hours=24)).sum())
median_price = f"${df['price_cad'].median():,.0f}" if df["price_cad"].notna().any() else "—"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Listings", f"{len(df):,}")
col2.metric("Active", f"{int((df['status'] == 'active').sum()):,}")
col3.metric("New (24h)", f"{new_24h:,}")
col4.metric("Median price", median_price)
col5.metric("Gone (likely sold)", f"{int((df['status'] == 'gone').sum()):,}")

# ── Sidebar filters ────────────────────────────────────────────────────────
st.sidebar.header("Filters")

sources = sorted(df["source"].dropna().unique())
picked_sources = st.sidebar.multiselect("Source", sources, default=sources)

top_products = df["product_key"].value_counts().head(40).index.tolist()
picked_products = st.sidebar.multiselect("Product", top_products)

keyword = st.sidebar.text_input("Title contains", "")

prices = df["price_cad"].dropna()
if not prices.empty:
    lo, hi = float(prices.min()), float(prices.max())
    price_range = st.sidebar.slider("Price ($)", lo, hi, (lo, hi))
else:
    price_range = (0.0, 0.0)

posted_within = st.sidebar.selectbox(
    "Posted within", ["Any time", "24 hours", "3 days", "7 days", "30 days"])

hide_flagged = st.sidebar.checkbox("Hide accessories / lots", value=True)
hide_pickup = st.sidebar.checkbox("Hide pickup-only listings", value=True)
include_gone = st.sidebar.checkbox("Include gone listings", value=True)

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh data", width="stretch"):
    load_listings.clear()
    st.rerun()

# ── Apply filters ──────────────────────────────────────────────────────────
view = df.copy()
if picked_sources:
    view = view[view["source"].isin(picked_sources)]
if picked_products:
    view = view[view["product_key"].isin(picked_products)]
if keyword:
    view = view[view["title"].str.contains(keyword, case=False, na=False)]
view = view[view["price_cad"].between(price_range[0], price_range[1])]

posted_days = {"24 hours": 1, "3 days": 3, "7 days": 7, "30 days": 30}.get(posted_within)
if posted_days:
    view = view[view["date_posted"] >= now - pd.Timedelta(days=posted_days)]
if hide_flagged:
    view = view[~view["flagged"]]
if hide_pickup:
    view = view[view["shipping"] != "pickup_only"]
if not include_gone:
    view = view[view["status"] == "active"]

# ── Charts ─────────────────────────────────────────────────────────────────
chart_left, chart_right = st.columns(2)
with chart_left:
    if view["price_cad"].notna().any():
        fig = px.histogram(view, x="price_cad", nbins=30,
                           title="Price distribution")
        fig.update_layout(xaxis_title="Price (CAD)", yaxis_title="Listings",
                          margin=dict(t=40, b=10))
        st.plotly_chart(fig, width="stretch")
with chart_right:
    by_source = view.groupby("source", as_index=False).size()
    if not by_source.empty:
        fig = px.bar(by_source, x="source", y="size", title="Listings by source")
        fig.update_layout(xaxis_title="", yaxis_title="Listings",
                          margin=dict(t=40, b=10))
        st.plotly_chart(fig, width="stretch")

new_by_day = (view.dropna(subset=["first_seen_at"])
              .assign(day=lambda d: d["first_seen_at"].dt.date)
              .groupby("day", as_index=False).size())
if not new_by_day.empty:
    fig = px.line(new_by_day, x="day", y="size", markers=True,
                  title="Listings first seen per day")
    fig.update_layout(xaxis_title="", yaxis_title="New listings",
                      margin=dict(t=40, b=10))
    st.plotly_chart(fig, width="stretch")

# ── Table ──────────────────────────────────────────────────────────────────
st.subheader(f"Listings ({len(view):,} of {len(df):,})")

table = view[[
    "product_key", "title", "price_cad", "landed", "shipping", "condition",
    "price_drop", "source", "location", "date_posted", "first_seen_at",
    "status", "flag_reason", "url",
]].rename(columns={
    "product_key": "Product", "title": "Title", "price_cad": "Price",
    "landed": "Landed (est.)", "shipping": "Shipping", "condition": "Condition",
    "price_drop": "Price drop", "source": "Source", "location": "Location",
    "date_posted": "Posted", "first_seen_at": "First seen", "status": "Status",
    "flag_reason": "Flagged", "url": "Link",
})
table["Price drop"] = table["Price drop"].astype(bool)

st.dataframe(
    table,
    width="stretch",
    hide_index=True,
    height=520,
    column_config={
        "Price": st.column_config.NumberColumn(format="$%.0f"),
        "Landed (est.)": st.column_config.NumberColumn(format="$%.0f"),
        "Link": st.column_config.LinkColumn(display_text="open"),
        "Posted": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        "First seen": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
    },
)

st.download_button("Download filtered listings (CSV)",
                   table.to_csv(index=False).encode("utf-8"),
                   file_name="deal-sniper-listings.csv", mime="text/csv")

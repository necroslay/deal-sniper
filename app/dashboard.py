"""Streamlit viewer for scraped marketplace listings.

    python -m app.scrape              # fill the database
    streamlit run app/dashboard.py    # browse it

Shows every listing with filters for source, product, price and recency, plus a
toggle to hide listings the guard flagged as accessories or lots.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.db.store import DB, Store

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
    return df


st.title("💰 Deal Sniper")
st.caption("Scraped second-hand computer parts — Kijiji + Craigslist (GTA)")

df = load_listings(DB_PATH)

if df.empty:
    st.info(
        "No listings yet. Populate the database first:\n\n"
        "```\npython -m app.scrape\n```"
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
include_gone = st.sidebar.checkbox("Include gone listings", value=True)

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh data", use_container_width=True):
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
    "product_key", "title", "price_cad", "source", "location",
    "date_posted", "first_seen_at", "status", "flag_reason", "url",
]].rename(columns={
    "product_key": "Product", "title": "Title", "price_cad": "Price",
    "source": "Source", "location": "Location", "date_posted": "Posted",
    "first_seen_at": "First seen", "status": "Status",
    "flag_reason": "Flagged", "url": "Link",
})

st.dataframe(
    table,
    use_container_width=True,
    hide_index=True,
    height=520,
    column_config={
        "Price": st.column_config.NumberColumn(format="$%.0f"),
        "Link": st.column_config.LinkColumn(display_text="open"),
        "Posted": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        "First seen": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
    },
)

st.download_button("Download filtered listings (CSV)",
                   table.to_csv(index=False).encode("utf-8"),
                   file_name="deal-sniper-listings.csv", mime="text/csv")

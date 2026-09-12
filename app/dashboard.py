"""Streamlit dashboard for the resale valuation engine."""
import sys, pathlib, sqlite3, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import streamlit as st
import pandas as pd
import plotly.express as px
from app.db.store import DB

st.set_page_config(page_title="Deal Sniper Dashboard", layout="wide")
st.title("💰 Deal Sniper Dashboard")

# Open connection fresh each run (no thread-safe caching with SQLite)
conn = sqlite3.connect(DB, check_same_thread=False)
conn.row_factory = sqlite3.Row

# Sidebar filters
st.sidebar.header("Filters")
products = [r[0] for r in conn.execute("SELECT DISTINCT canonical_product FROM valuation_decisions ORDER BY canonical_product").fetchall()]
selected_product = st.sidebar.selectbox("Product", ["All"] + products)
levels = [r[0] for r in conn.execute("SELECT DISTINCT confidence_level FROM valuation_decisions ORDER BY confidence_level").fetchall()]
selected_level = st.sidebar.selectbox("Confidence", ["All"] + levels)
min_score, max_score = st.sidebar.slider("Deal Score", 0.0, 100.0, (0.0, 100.0))

# Build query
query = "SELECT * FROM valuation_decisions WHERE 1=1"
params = []
if selected_product != "All": query += " AND canonical_product = ?"; params.append(selected_product)
if selected_level != "All": query += " AND confidence_level = ?"; params.append(selected_level)
query += " AND deal_score >= ? AND deal_score <= ?"; params.extend([min_score, max_score])
query += " ORDER BY deal_score DESC LIMIT 200"
rows = conn.execute(query, params).fetchall()

# Stats cards
col1, col2, col3, col4, col5 = st.columns(5)
# Score distribution
scores = [r["deal_score"] for r in rows if r["deal_score"] is not None]
if scores:
    fig = px.histogram(x=scores, nbins=20, title="Score Distribution", labels={"x": "Score"})
    st.plotly_chart(fig, width='stretch')

# Deals table
st.subheader(f"Deals ({len(rows)} matching)")
data = []
for r in rows:
    try:
        payload = json.loads(r["decision_payload"]) if r["decision_payload"] else {}
        rl = payload.get("raw_listing", {}); fv = payload.get("fair_value", {})
        ds = payload.get("deal_score", {}); rv = payload.get("realized_value", {})
        data.append({"Product": r["canonical_product"], "Score": f'{r["deal_score"]:.0f}',
            "FMV": f'${fv.get("fmv_cad", 0):,.0f}', "ERV": f'${rv.get("erv_cad", 0):,.0f}',
            "Profit": f'${ds.get("expected_profit_cad", 0):,.0f}',
            "Margin": f'{ds.get("margin_pct", 0):.0f}%',
            "Confidence": r["confidence_level"], "Alert": "🚨" if r["alert_sent"] else "",
            "Title": rl.get("title", "")[:60], "Price": f'${rl.get("price_cad", 0):,.0f}',
            "Location": rl.get("location", ""), "Condition": fv.get("condition", "")})
    except: pass
if data:
    st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True,
                 column_order=["Product","Score","FMV","ERV","Profit","Margin","Confidence","Alert","Title","Price","Location","Condition"])
else:
    st.info("No deals match. Run the pipeline first!")

# Top deals
st.subheader("Top Deals by Profit")
top = conn.execute("SELECT * FROM valuation_decisions ORDER BY deal_score DESC LIMIT 20").fetchall()
td = []
for r in top:
    try:
        p = json.loads(r["decision_payload"]) if r["decision_payload"] else {}
        ds = p.get("deal_score", {}); rl = p.get("raw_listing", {}); fv = p.get("fair_value", {})
        td.append({"Product": r["canonical_product"], "Score": f'{r["deal_score"]:.0f}',
            "Profit": f'${ds.get("expected_profit_cad", 0):,.0f}',
            "FMV": f'${fv.get("fmv_cad", 0):,.0f}', "Ask": f'${rl.get("price_cad", 0):,.0f}',
            "Margin": f'{ds.get("margin_pct", 0):.0f}%', "Confidence": r["confidence_level"],
            "Title": rl.get("title", "")[:60]})
    except: pass
if td:
    st.dataframe(pd.DataFrame(td), use_container_width=True, hide_index=True)

# Source health
st.subheader("Source Health")
health = conn.execute("SELECT * FROM source_health").fetchall()
if health:
    hd = [{"Source": r["source"], "Last Success": (r["last_success_at"] or "")[:19],
        "Last Failure": (r["last_failure_at"] or "")[:19],
        "Failures": r["consecutive_failures"], "Blocked": "🔴" if r["is_blocked"] else "🟢"} for r in health]
    st.dataframe(pd.DataFrame(hd), use_container_width=True, hide_index=True)
else:
    st.info("No source health data yet.")

if st.button("Refresh"):
    st.rerun()
col1.metric("Total Decisions", conn.execute("SELECT COUNT(*) FROM valuation_decisions").fetchone()[0])
col2.metric("Alerts Sent", conn.execute("SELECT COUNT(*) FROM valuation_decisions WHERE alert_sent=1").fetchone()[0])
col3.metric("Avg Score", f"{conn.execute('SELECT AVG(deal_score) FROM valuation_decisions').fetchone()[0]:.0f}")
col4.metric("Products", conn.execute("SELECT COUNT(DISTINCT canonical_product) FROM valuation_decisions").fetchone()[0])
col5.metric("Sources", conn.execute("SELECT COUNT(DISTINCT source) FROM raw_listings").fetchone()[0])
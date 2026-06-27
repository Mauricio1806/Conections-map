# -*- coding: utf-8 -*-
"""
app/dashboard.py  –  LinkedIn Network Intelligence Dashboard
Run with:  streamlit run app/dashboard.py
"""

import sys
import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUTS   = ROOT / "outputs"
REPORTS   = ROOT / "reports"

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LinkedIn Network Intelligence",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── colour palette ────────────────────────────────────────────────────────────
PALETTE = {
    "BRAZIL":             "#009c3b",
    "LATAM_USD":          "#f4c430",
    "US_CANADA_NEARSHORE":"#002868",
    "SPAIN_EU":           "#c60b1e",
    "EUROPE":             "#003399",
    "UNKNOWN":            "#aaaaaa",
}

URGENCY_COLOURS = {
    "Critical":  "#d62728",
    "High":      "#ff7f0e",
    "Medium":    "#ffdf00",
    "Low":       "#2ca02c",
    "Saturated": "#17becf",
}

# ── data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_classified() -> pd.DataFrame:
    p = OUTPUTS / "classified_connections.csv"
    if not p.exists():
        st.error("classified_connections.csv not found. Run the pipeline first.")
        st.stop()
    return pd.read_csv(p, dtype=str, low_memory=False)


@st.cache_data(ttl=300)
def load_dashboard_json() -> dict | None:
    p = OUTPUTS / "dashboard_metrics.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=300)
def load_gap() -> pd.DataFrame:
    p = OUTPUTS / "strategic_gap_report.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


@st.cache_data(ttl=300)
def load_action_plan(name: str) -> pd.DataFrame:
    p = OUTPUTS / f"action_plan_{name}_days.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


# ── helpers ───────────────────────────────────────────────────────────────────
def metric_card(label: str, value, delta: str = "", help_text: str = ""):
    st.metric(label=label, value=value, delta=delta, help=help_text)


def score_gauge(title: str, value: float, max_val: float = 100):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": title, "font": {"size": 14}},
        gauge={
            "axis": {"range": [0, max_val]},
            "bar":  {"color": "#1f77b4"},
            "steps": [
                {"range": [0, 30],      "color": "#ffcccc"},
                {"range": [30, 60],     "color": "#fff3cd"},
                {"range": [60, max_val],"color": "#d4edda"},
            ],
            "threshold": {
                "line": {"color": "red", "width": 3},
                "thickness": 0.75,
                "value": value,
            },
        },
    ))
    fig.update_layout(height=200, margin=dict(t=40, b=0, l=20, r=20))
    return fig


def urgency_badge(level: str) -> str:
    colours = {
        "Critical":  "red",
        "High":      "orange",
        "Medium":    "goldenrod",
        "Low":       "green",
        "Saturated": "teal",
    }
    c = colours.get(level, "grey")
    return f'<span style="color:{c};font-weight:bold">{level}</span>'


# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/000000/linkedin.png", width=60)
    st.title("Network Intel")
    st.caption("LinkedIn Connections Heatmap")
    st.divider()
    page = st.radio(
        "Navigate",
        [
            "1. Executive Overview",
            "2. Network Heatmap",
            "3. Strategic Gap",
            "4. Action Plan",
            "5. Top Priority Contacts",
            "6. Company Intelligence",
            "7. Data Quality",
        ],
    )
    st.divider()
    st.caption("Run `python src/build_strategy_layer.py` to refresh data.")


# ── load data ─────────────────────────────────────────────────────────────────
df_raw    = load_classified()
json_data = load_dashboard_json()
gap_df    = load_gap()

# coerce numeric
for col in ["priority_score", "market_confidence"]:
    if col in df_raw.columns:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce").fillna(0)

kpis = json_data["kpis"] if json_data else {}


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1: Executive Overview
# ══════════════════════════════════════════════════════════════════════════════
if page == "1. Executive Overview":
    st.title("Executive Network Overview")
    st.caption(f"Report date: {kpis.get('report_date', 'N/A')}")

    # ── score gauges ──────────────────────────────────────────────────────────
    g1, g2, g3 = st.columns(3)
    with g1:
        st.plotly_chart(
            score_gauge("USD Opportunity Score",
                        kpis.get("usd_opportunity_score", 0)),
            use_container_width=True,
        )
    with g2:
        st.plotly_chart(
            score_gauge("Spain/EU Readiness Score",
                        kpis.get("spain_readiness_score", 0)),
            use_container_width=True,
        )
    with g3:
        st.plotly_chart(
            score_gauge("Market Readiness Score",
                        kpis.get("market_readiness_score", 0)),
            use_container_width=True,
        )

    st.divider()

    # ── KPI cards row 1 ───────────────────────────────────────────────────────
    st.subheader("Network Size & Priority")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Connections",    f"{kpis.get('total_connections', 0):,}")
    c2.metric("High Priority",
              f"{kpis.get('high_priority', 0):,}",
              delta=f"{kpis.get('high_priority_pct', 0)}%")
    c3.metric("Medium Priority",
              f"{kpis.get('medium_priority', 0):,}",
              delta=f"{kpis.get('medium_priority_pct', 0)}%")
    c4.metric("Connected Last 30d",   kpis.get("connected_last_30_days", 0))
    c5.metric("Connected Last 90d",   kpis.get("connected_last_90_days", 0))

    # ── KPI cards row 2 ───────────────────────────────────────────────────────
    st.subheader("Key Persona Groups")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Recruiters",         kpis.get("recruiters_total", 0))
    c2.metric("Talent / HR",        kpis.get("talent_hr_total", 0))
    c3.metric("Hiring Managers",    kpis.get("hiring_managers_total", 0))
    c4.metric("Data Leaders",       kpis.get("data_leaders_total", 0))
    c5.metric("Data Peers",         kpis.get("data_peers_total", 0))

    # ── KPI cards row 3 ───────────────────────────────────────────────────────
    st.subheader("Market Distribution")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Brazil",         kpis.get("brazil_count", 0))
    c2.metric("LATAM USD",      kpis.get("latam_usd_count", 0))
    c3.metric("US/CA Nearshore",kpis.get("us_nearshore_count", 0))
    c4.metric("Spain/EU",       kpis.get("spain_eu_count", 0))
    c5.metric("Europe",         kpis.get("europe_count", 0))
    c6.metric("Unknown",        kpis.get("unknown_count", 0))
    c7.metric("Unknown %",      f"{kpis.get('unknown_pct', 0)}%")

    st.divider()

    # ── market pie chart ──────────────────────────────────────────────────────
    st.subheader("Market Distribution (Identified Connections Only)")
    market_data = {
        k: v for k, v in {
            "BRAZIL":              kpis.get("brazil_count", 0),
            "LATAM_USD":           kpis.get("latam_usd_count", 0),
            "US_CANADA_NEARSHORE": kpis.get("us_nearshore_count", 0),
            "SPAIN_EU":            kpis.get("spain_eu_count", 0),
            "EUROPE":              kpis.get("europe_count", 0),
        }.items() if v > 0
    }
    col_pie, col_bar = st.columns(2)
    with col_pie:
        fig = px.pie(
            values=list(market_data.values()),
            names=list(market_data.keys()),
            color=list(market_data.keys()),
            color_discrete_map=PALETTE,
            hole=0.45,
            title="Identified Market Share (excl. Unknown)",
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

    with col_bar:
        top_personas = kpis.get("top_personas", {})
        fig2 = px.bar(
            x=list(top_personas.values()),
            y=list(top_personas.keys()),
            orientation="h",
            title="Top 10 Personas in Network",
            labels={"x": "Count", "y": "Persona"},
            color=list(top_personas.values()),
            color_continuous_scale="Blues",
        )
        fig2.update_layout(coloraxis_showscale=False, yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig2, use_container_width=True)

    # ── concentration risk ────────────────────────────────────────────────────
    st.subheader("Network Concentration Risk")
    flags = kpis.get("concentration_flags", [])
    for flag in flags:
        if "No critical" in flag:
            st.success(f"✅ {flag}")
        elif "High unknown" in flag or "high" in flag.lower():
            st.warning(f"⚠️ {flag}")
        else:
            st.info(f"ℹ️ {flag}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2: Network Heatmap
# ══════════════════════════════════════════════════════════════════════════════
elif page == "2. Network Heatmap":
    st.title("Network Heatmap")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Persona × Market",
        "Area × Market",
        "Seniority × Market",
        "Company × Persona",
        "Persona × Priority",
    ])

    def _heatmap_from_df(df_in, index, columns, title):
        pivot = (
            df_in.groupby([index, columns])
            .size()
            .reset_index(name="count")
            .pivot(index=index, columns=columns, values="count")
            .fillna(0)
        )
        fig = px.imshow(
            pivot,
            text_auto=True,
            aspect="auto",
            color_continuous_scale="Blues",
            title=title,
        )
        fig.update_layout(height=500)
        return fig

    with tab1:
        fig = _heatmap_from_df(df_raw, "persona", "strategic_market", "Persona × Strategic Market")
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        fig = _heatmap_from_df(df_raw, "area", "strategic_market", "Area × Strategic Market")
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        fig = _heatmap_from_df(df_raw, "seniority", "strategic_market", "Seniority × Strategic Market")
        st.plotly_chart(fig, use_container_width=True)

    with tab4:
        top_companies = df_raw["company_clean"].value_counts().head(20).index.tolist()
        df_top = df_raw[df_raw["company_clean"].isin(top_companies)]
        fig = _heatmap_from_df(df_top, "company_clean", "persona", "Top 20 Companies × Persona")
        st.plotly_chart(fig, use_container_width=True)

    with tab5:
        df_raw2 = df_raw.copy()
        df_raw2["priority_band"] = pd.cut(
            df_raw2["priority_score"],
            bins=[-1, 39, 69, 100],
            labels=["Low (<40)", "Medium (40-69)", "High (>=70)"]
        ).astype(str)
        fig = _heatmap_from_df(df_raw2, "persona", "priority_band", "Persona × Priority Band")
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3: Strategic Gap Dashboard
# ══════════════════════════════════════════════════════════════════════════════
elif page == "3. Strategic Gap":
    st.title("Strategic Gap Dashboard")
    st.caption("Where your network is overrepresented or underrepresented vs. career targets.")

    gap_matrix_path = OUTPUTS / "connection_gap_matrix.csv"
    if gap_matrix_path.exists():
        gm = pd.read_csv(gap_matrix_path)
    else:
        gm = gap_df

    if gm.empty:
        st.warning("Gap data not found. Run `python src/build_strategy_layer.py` first.")
    else:
        # ── filter controls ───────────────────────────────────────────────────
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            urgency_filter = st.multiselect(
                "Filter by Urgency",
                options=["Critical", "High", "Medium", "Low", "Saturated"],
                default=["Critical", "High"],
            )
        with col_f2:
            market_filter = st.multiselect(
                "Filter by Market",
                options=gm["market"].unique().tolist() if "market" in gm.columns else [],
                default=gm["market"].unique().tolist() if "market" in gm.columns else [],
            )

        col_filt = "urgency_level" if "urgency_level" in gm.columns else "priority"
        mkt_col  = "market" if "market" in gm.columns else "strategic_market"

        filtered = gm
        if urgency_filter and col_filt in gm.columns:
            filtered = filtered[filtered[col_filt].isin(urgency_filter)]
        if market_filter and mkt_col in gm.columns:
            filtered = filtered[filtered[mkt_col].isin(market_filter)]

        # ── gap bar chart ─────────────────────────────────────────────────────
        gap_col = "gap_count" if "gap_count" in filtered.columns else "short_term_gap"
        persona_col = "persona"
        label_col   = mkt_col

        if gap_col in filtered.columns and not filtered.empty:
            filtered_sorted = filtered.sort_values(gap_col, ascending=True).tail(20)
            filtered_sorted["label"] = (
                filtered_sorted[label_col] + " – " + filtered_sorted[persona_col]
            )
            fig = px.bar(
                filtered_sorted,
                x=gap_col,
                y="label",
                orientation="h",
                color=col_filt,
                color_discrete_map=URGENCY_COLOURS,
                title="Connections Needed by Market/Persona",
                labels={gap_col: "Gap Count", "label": ""},
            )
            fig.update_layout(height=550, yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig, use_container_width=True)

        # ── gap table ─────────────────────────────────────────────────────────
        st.subheader("Gap Detail Table")
        display_cols = [c for c in [
            mkt_col, "persona", "current_count", "target_count",
            gap_col, "gap_percentage", col_filt, "timeframe", "strategic_reason"
        ] if c in filtered.columns]
        st.dataframe(filtered[display_cols], use_container_width=True, height=400)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4: Action Plan
# ══════════════════════════════════════════════════════════════════════════════
elif page == "4. Action Plan":
    st.title("30 / 60 / 90 Day Action Plan")

    tab30, tab60, tab90 = st.tabs(["30 Days", "60 Days", "90 Days"])

    def _render_plan(plan_df: pd.DataFrame, label: str):
        if plan_df.empty:
            st.info(f"No {label} plan data. Run `python src/build_strategy_layer.py`.")
            return

        urgency_col = "urgency_level" if "urgency_level" in plan_df.columns else "priority"
        gap_col     = "gap_count" if "gap_count" in plan_df.columns else "short_term_gap"

        # summary counts
        if urgency_col in plan_df.columns:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Critical",  int((plan_df[urgency_col] == "Critical").sum()))
            c2.metric("High",      int((plan_df[urgency_col] == "High").sum()))
            c3.metric("Medium",    int((plan_df[urgency_col] == "Medium").sum()))
            c4.metric("Low",       int((plan_df[urgency_col] == "Low").sum()))
            c5.metric("Saturated", int((plan_df[urgency_col] == "Saturated").sum()))

        # bar chart
        if gap_col in plan_df.columns:
            plan_df["label"] = plan_df["market"] + " – " + plan_df["persona"]
            fig = px.bar(
                plan_df.sort_values(gap_col, ascending=False),
                x="label", y=gap_col,
                color=urgency_col,
                color_discrete_map=URGENCY_COLOURS,
                title=f"{label}: Connections Needed",
                labels={gap_col: "Gap", "label": "Market – Persona"},
            )
            fig.update_layout(xaxis_tickangle=-40, height=450)
            st.plotly_chart(fig, use_container_width=True)

        # detail table
        st.dataframe(plan_df, use_container_width=True, height=350)

    with tab30:
        st.subheader("Next 30 Days — USD Remote Job Focus")
        st.info("Focus: Build LATAM/USD and US/Canada recruiter + hiring manager pipeline.")
        _render_plan(load_action_plan("30"), "30-Day")

    with tab60:
        st.subheader("Next 60 Days — Extend USD + Start Spain/EU")
        st.info("Focus: Maintain USD pipeline, begin Spain/EU recruiter network.")
        _render_plan(load_action_plan("60"), "60-Day")

    with tab90:
        st.subheader("Next 90 Days — Balance USD + EU Positioning")
        st.info("Focus: Balance USD income stability with Spain/Europe readiness.")
        _render_plan(load_action_plan("90"), "90-Day")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5: Top Priority Contacts
# ══════════════════════════════════════════════════════════════════════════════
elif page == "5. Top Priority Contacts":
    st.title("Top Priority Contacts")

    # ── filters ───────────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        min_score = st.slider("Min Priority Score", 0, 100, 60)
    with col2:
        persona_opts = sorted(df_raw["persona"].dropna().unique().tolist())
        sel_persona  = st.multiselect("Filter Persona", persona_opts, default=[])
    with col3:
        market_opts = sorted(df_raw["strategic_market"].dropna().unique().tolist())
        sel_market  = st.multiselect("Filter Market", market_opts, default=[])
    with col4:
        n_results = st.number_input("Max Results", 10, 200, 50)

    filtered = df_raw[df_raw["priority_score"] >= min_score]
    if sel_persona:
        filtered = filtered[filtered["persona"].isin(sel_persona)]
    if sel_market:
        filtered = filtered[filtered["strategic_market"].isin(sel_market)]

    top = filtered.sort_values("priority_score", ascending=False).head(int(n_results))

    st.metric("Contacts Shown", len(top))

    display_cols = [c for c in [
        "full_name", "company_clean", "position_clean",
        "persona", "area", "seniority", "strategic_market",
        "priority_score", "recommended_action", "connected_on_clean", "url"
    ] if c in top.columns]

    # Colour-code by score
    def _colour_score(val):
        try:
            v = float(val)
            if v >= 70: return "background-color: #d4edda"
            if v >= 40: return "background-color: #fff3cd"
            return "background-color: #f8d7da"
        except:
            return ""

    styled = top[display_cols].style.applymap(_colour_score, subset=["priority_score"])
    st.dataframe(styled, use_container_width=True, height=500)

    # Score distribution
    fig = px.histogram(
        top, x="priority_score", nbins=20,
        title="Priority Score Distribution (Filtered)",
        color_discrete_sequence=["#1f77b4"],
    )
    st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 6: Company Intelligence
# ══════════════════════════════════════════════════════════════════════════════
elif page == "6. Company Intelligence":
    st.title("Company Intelligence")

    company_tab1, company_tab2, company_tab3, company_tab4, company_tab5 = st.tabs([
        "All Companies",
        "Recruiting/Staffing",
        "Data Companies",
        "LATAM USD",
        "Spain/EU",
    ])

    def _company_bar(data, x, y, title, n=25):
        df_c = pd.DataFrame(data).head(n)
        if df_c.empty:
            st.info("No data.")
            return
        fig = px.bar(
            df_c.sort_values(x, ascending=True).tail(n),
            x=x, y=y, orientation="h",
            title=title, labels={x: "Count", y: ""},
            color=x, color_continuous_scale="Blues",
        )
        fig.update_layout(coloraxis_showscale=False, height=500,
                          yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)

    intel = (json_data or {}).get("company_intel", {})

    with company_tab1:
        st.subheader("Top 50 Companies by Connection Count")
        data = intel.get("top_companies", [])
        if data:
            _company_bar(data, "count", "company_clean", "Top Companies")
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            # fallback
            top_c = (
                df_raw[df_raw["company_clean"].str.strip() != ""]
                ["company_clean"].value_counts().head(50)
                .reset_index()
            )
            top_c.columns = ["company_clean", "count"]
            _company_bar(top_c.to_dict("records"), "count", "company_clean", "Top Companies")

    with company_tab2:
        st.subheader("Recruiting & Staffing Companies")
        data = intel.get("top_recruiting_companies", [])
        if data:
            _company_bar(data, "recruiter_count", "company_clean",
                         "Recruiting/Staffing Companies")
        else:
            rec = df_raw[df_raw["persona"].isin(["Recruiter","Talent Acquisition","Sourcer"])]
            top_r = rec["company_clean"].value_counts().head(30).reset_index()
            top_r.columns = ["company_clean", "recruiter_count"]
            _company_bar(top_r.to_dict("records"), "recruiter_count",
                         "company_clean", "Top Recruiting Companies")

    with company_tab3:
        st.subheader("Top Data-Focused Companies")
        data = intel.get("top_data_companies", [])
        if data:
            _company_bar(data, "data_count", "company_clean", "Data Companies")
        else:
            data_mask = df_raw["area"].isin(["Data Engineering","Analytics","BI","Data Science / AI"])
            top_d = df_raw[data_mask]["company_clean"].value_counts().head(30).reset_index()
            top_d.columns = ["company_clean", "data_count"]
            _company_bar(top_d.to_dict("records"), "data_count",
                         "company_clean", "Data Companies")

    with company_tab4:
        st.subheader("LATAM USD Relevant Companies")
        st.caption("Companies with connections classified in the LATAM_USD market.")
        data = intel.get("top_latam_companies", [])
        if data:
            _company_bar(data, "count", "company_clean", "LATAM USD Companies")
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            latam = df_raw[df_raw["strategic_market"] == "LATAM_USD"]
            top_l = latam["company_clean"].value_counts().head(25).reset_index()
            top_l.columns = ["company_clean", "count"]
            _company_bar(top_l.to_dict("records"), "count", "company_clean", "LATAM USD Companies")

    with company_tab5:
        st.subheader("Spain/EU Relevant Companies")
        st.caption("Companies with connections classified in SPAIN_EU or EUROPE markets.")
        data = intel.get("top_spain_companies", [])
        if data:
            _company_bar(data, "count", "company_clean", "Spain/EU Companies")
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            spain = df_raw[df_raw["strategic_market"].isin(["SPAIN_EU","EUROPE"])]
            top_s = spain["company_clean"].value_counts().head(25).reset_index()
            top_s.columns = ["company_clean", "count"]
            _company_bar(top_s.to_dict("records"), "count", "company_clean", "Spain/EU Companies")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 7: Data Quality & Limitations
# ══════════════════════════════════════════════════════════════════════════════
elif page == "7. Data Quality":
    st.title("Data Quality & Limitations")

    total = kpis.get("total_connections", len(df_raw))

    st.subheader("Completeness Metrics")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Missing Company",
              f"{kpis.get('missing_company_count', 0):,}",
              delta=f"{kpis.get('missing_company_pct', 0)}%")
    c2.metric("Missing Position",
              f"{kpis.get('missing_position_count', 0):,}",
              delta=f"{kpis.get('missing_position_pct', 0)}%")
    c3.metric("Unknown Market",
              f"{kpis.get('unknown_count', 0):,}",
              delta=f"{kpis.get('unknown_pct', 0)}%")
    c4.metric("Inferred Markets",
              f"{total - kpis.get('unknown_count', 0):,}")

    # Confidence distribution
    st.subheader("Market Inference Confidence Distribution")
    if "market_confidence" in df_raw.columns:
        conf_counts = df_raw["market_confidence"].value_counts().reset_index()
        conf_counts.columns = ["confidence", "count"]
        conf_counts["confidence"] = conf_counts["confidence"].astype(str)
        fig = px.pie(
            conf_counts,
            values="count", names="confidence",
            title="Inference Confidence Levels",
            color_discrete_sequence=px.colors.sequential.Blues_r,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Why Is Market Unknown for 82% of Connections?")
    st.markdown("""
**LinkedIn's data export limitation:**

The LinkedIn Connections CSV export does **not** include location data for your connections.
Location fields (city, country, region) are only visible on individual profile pages
and cannot be bulk-exported under LinkedIn's Terms of Service.

**How market inference works here:**
- Company name → matched against keyword lists per market
- Job title → keywords like 'LATAM', 'nearshore', 'España', etc.
- Company brand signals → e.g. 'AgileEngine' → LATAM_USD

**Confidence levels:**
- `0.0` = No keyword matched → UNKNOWN
- `0.5` = 1 keyword matched
- `0.7` = 2 keywords matched
- `0.9` = 3+ keywords matched

**Implication:** The UNKNOWN group (8,817 connections) is **not** irrelevant.
Many of them may be in your target markets — their company just didn't have a keyword match.
The classified subset (1,963 connections) is a **lower-bound estimate** of your strategic network.
    """)

    st.subheader("Inference Reasons Sample")
    if "inference_reason" in df_raw.columns:
        sample = (
            df_raw[df_raw["strategic_market"] != "UNKNOWN"][["full_name","company_clean",
            "strategic_market","market_confidence","inference_reason"]]
            .head(30)
        )
        st.dataframe(sample, use_container_width=True)

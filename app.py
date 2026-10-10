import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np

st.set_page_config(page_title="World GDP Map", layout="wide")
st.title("🌍 World GDP Map — Each Country in a Different Color")

# Load dataset
@st.cache_data
def load_data():
    return pd.read_csv(
        "https://raw.githubusercontent.com/plotly/datasets/master/2014_world_gdp_with_codes.csv"
    )

df = load_data()

# Assign a unique numeric index per country for distinct coloring
df = df.reset_index(drop=True)
df["color_id"] = df.index  # each country gets a unique number

# Use a large discrete-like colorscale by mapping color_id → 0..1
n = len(df)
df["color_norm"] = df["color_id"] / (n - 1)

# Build custom colorscale with one color per country using HSL spread
def make_colorscale(n):
    colors = []
    for i in range(n):
        hue = int((i * 360 / n) % 360)
        sat = 70 + (i % 3) * 10          # vary saturation slightly
        lit = 45 + (i % 5) * 5           # vary lightness slightly
        colors.append(f"hsl({hue},{sat}%,{lit}%)")
    # Plotly colorscale: list of [position, color]
    return [[i / (n - 1), c] for i, c in enumerate(colors)]

colorscale = make_colorscale(n)

fig = go.Figure(
    data=go.Choropleth(
        locations=df["CODE"],
        z=df["color_norm"],           # used only for color mapping
        text=df["COUNTRY"],
        customdata=df["GDP (BILLIONS)"],
        colorscale=colorscale,
        zmin=0,
        zmax=1,
        showscale=False,              # hide colorbar (not meaningful here)
        marker_line_color="white",
        marker_line_width=0.6,
        hovertemplate=(
            "<b>%{text}</b><br>"
            "GDP: $%{customdata:.1f}B<br>"
            "<extra></extra>"
        ),
    )
)

fig.update_layout(
    title=dict(
        text="World Map — Unique Color per Country",
        font=dict(size=20),
        x=0.5,
    ),
    geo=dict(
        showframe=False,
        showcoastlines=True,
        coastlinecolor="white",
        showland=True,
        landcolor="#1a1a2e",
        showocean=True,
        oceancolor="#0d1b2a",
        showlakes=True,
        lakecolor="#0d1b2a",
        projection_type="equirectangular",
        bgcolor="#0d1b2a",
    ),
    paper_bgcolor="#0d1b2a",
    plot_bgcolor="#0d1b2a",
    font=dict(color="white"),
    margin=dict(l=0, r=0, t=50, b=0),
    height=600,
    annotations=[
        dict(
            x=0.55, y=0.02,
            xref="paper", yref="paper",
            text='Source: <a href="https://www.cia.gov/library/publications/the-world-factbook/" style="color:#7ec8e3">CIA World Factbook</a>',
            showarrow=False,
            font=dict(color="#aaaaaa", size=10),
        )
    ],
)

st.plotly_chart(fig, use_container_width=True)

# ── GDP Table below map ──────────────────────────────────────────────────────
st.divider()
st.subheader("📊 GDP Data by Country")

col1, col2 = st.columns([2, 1])
with col1:
    search = st.text_input("Search country", "")
with col2:
    top_n = st.selectbox("Show top N by GDP", [10, 20, 50, "All"], index=1)

filtered = df[df["COUNTRY"].str.contains(search, case=False)] if search else df.copy()
filtered = filtered.sort_values("GDP (BILLIONS)", ascending=False)
if top_n != "All":
    filtered = filtered.head(int(top_n))

st.dataframe(
    filtered[["COUNTRY", "CODE", "GDP (BILLIONS)"]].rename(columns={
        "COUNTRY": "Country",
        "CODE": "ISO Code",
        "GDP (BILLIONS)": "GDP (Billions USD)",
    }).reset_index(drop=True),
    use_container_width=True,
    height=350,
)

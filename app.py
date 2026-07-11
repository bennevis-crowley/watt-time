"""
Personal electricity price viewer (DK1 / DK2).

No server, no scheduler, no database - just this one file.
Deploy for free on Streamlit Community Cloud (share.streamlit.io)
and open the resulting URL on your tablet.

Refresh behaviour: the data pull is cached and only re-fetched once
per day, right after 14:00 - which is when tomorrow's day-ahead
prices are typically published. Opening the app before 14:00 reuses
today's cached data; opening it after 14:00 triggers exactly one
fresh pull, and every open after that reuses it until the next day.
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

EDS_URL = "https://api.energidataservice.dk/dataset/DayAheadPrices"
PRICE_AREAS = ["DK1", "DK2"]
REFRESH_HOUR = 14


def _cache_bucket() -> str:
    """A string that only changes once per day, right after 14:00.
    Passing this into the cached fetch function means Streamlit will
    only actually re-fetch when it changes - i.e. once daily."""
    now = datetime.now()
    bucket_date = now.date() if now.hour >= REFRESH_HOUR else now.date() - timedelta(days=1)
    return bucket_date.isoformat()


@st.cache_data(show_spinner="Fetching latest prices...")
def fetch_prices(cache_bucket: str) -> pd.DataFrame:
    start = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")
    params = {
        "start": start,
        "end": end,
        "filter": '{"PriceArea":["DK1","DK2"]}',
        "sort": "TimeDK asc",
    }
    resp = requests.get(EDS_URL, params=params, timeout=30)
    resp.raise_for_status()
    records = resp.json().get("records", [])
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No data returned from Energi Data Service")
    df["HourDK"] = pd.to_datetime(df["TimeDK"])
    df["SpotPriceDKK"] = df["DayAheadPriceDKK"]
    return df


st.set_page_config(page_title="Electricity Prices", layout="wide")
st.title("⚡ Electricity Prices")

df = fetch_prices(_cache_bucket())

area = st.radio("Area", PRICE_AREAS, horizontal=True)
area_df = df[df["PriceArea"] == area].sort_values("HourDK")

now = pd.Timestamp.now()
future = area_df[area_df["HourDK"] >= now]

if not future.empty:
    current_ore_kwh = future.iloc[0]["SpotPriceDKK"] / 10
    st.metric("Current price", f"{current_ore_kwh:.1f} øre/kWh")

    period_hours = st.slider("Cheapest period length (hours)", min_value=1, max_value=6, value=1)
    period_length = period_hours * 4  # each row is a 15-minute period

    window_start = window_end = None
    if len(future) >= period_length:
        rolling_avg = future["SpotPriceDKK"].rolling(window=period_length).mean()
        end_idx = rolling_avg.idxmin()
        end_pos = future.index.get_loc(end_idx)
        start_pos = end_pos - period_length + 1
        window = future.iloc[start_pos : end_pos + 1]
        window_start = window.iloc[0]["HourDK"]
        window_end = window.iloc[-1]["HourDK"] + pd.Timedelta(minutes=15)
        avg_price = window["SpotPriceDKK"].mean() / 10
        st.caption(f"Cheapest {period_hours}-hour window: {window_start.strftime('%a %H:%M')}–{window_end.strftime('%H:%M')} at {avg_price:.1f} øre/kWh avg")
    else:
        st.caption("Not enough upcoming data for that period length yet.")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=future["HourDK"], y=future["SpotPriceDKK"] / 10, mode="lines", name="Price"))
    if window_start is not None:
        fig.add_vrect(x0=window_start, x1=window_end, fillcolor="green", opacity=0.2, line_width=0)
    fig.update_layout(yaxis_title="øre/kWh", margin=dict(l=0, r=0, t=10, b=0), height=350)
    st.plotly_chart(fig, use_container_width=True)

st.caption("Prices in øre/kWh. Refreshes once daily shortly after 14:00.")future = area_df[area_df["HourDK"] >= now]

if not future.empty:
    current_ore_kwh = future.iloc[0]["SpotPriceDKK"] / 10
    st.metric("Current price", f"{current_ore_kwh:.1f} øre/kWh")

    period = st.slider("Cheapest period length (hours)", min_value=1, max_value=6, value=1)

    if len(future) >= period:
        rolling_avg = future["SpotPriceDKK"].rolling(window=period).mean()
        end_idx = rolling_avg.idxmin()
        end_pos = future.index.get_loc(end_idx)
        start_pos = end_pos - period + 1
        window = future.iloc[start_pos : end_pos + 1]
        window_start = window.iloc[0]["HourDK"]
        window_end = window.iloc[-1]["HourDK"] + pd.Timedelta(hours=1)
        avg_price = window["SpotPriceDKK"].mean() / 10
        st.caption(f"Cheapest {period}-hour window: {window_start.strftime('%a %H:%M')}–{window_end.strftime('%H:%M')} at {avg_price:.1f} øre/kWh avg")
    else:
        st.caption("Not enough upcoming data for that period length yet.")

st.line_chart(future.set_index("HourDK")["SpotPriceDKK"] / 10)

st.caption("Prices in øre/kWh. Refreshes once daily shortly after 14:00.")

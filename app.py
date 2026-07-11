from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

EDS_URL = "https://api.energidataservice.dk/dataset/Elspotprices"
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
        "sort": "HourUTC asc",
    }
    resp = requests.get(EDS_URL, params=params, timeout=30)
    resp.raise_for_status()
    records = resp.json().get("records", [])
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No data returned from Energi Data Service")
    df["HourDK"] = pd.to_datetime(df["HourDK"])
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

    cheapest = future.loc[future["SpotPriceDKK"].idxmin()]
    st.caption(
        f"Cheapest upcoming hour: {cheapest['HourDK'].strftime('%a %H:%M')} "
        f"at {cheapest['SpotPriceDKK']/10:.1f} øre/kWh"
    )

st.line_chart(area_df.set_index("HourDK")["SpotPriceDKK"] / 10)

st.caption("Prices in øre/kWh. Refreshes once daily shortly after 14:00.")

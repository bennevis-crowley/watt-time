"""
Personal electricity price viewer (DK2 / Copenhagen) - spot price plus
DSO (Radius) and TSO (Energinet) tariffs.

No server, no scheduler, no database - just this one file.
Deploy for free on Streamlit Community Cloud (share.streamlit.io)
and open the resulting URL on your tablet.

Refresh behaviour: the data pull is cached and only re-fetched once
per day, right after 14:00 - which is when tomorrow's day-ahead
prices are typically published. Opening the app before 14:00 reuses
today's cached data; opening it after 14:00 triggers exactly one
fresh pull, and every open after that reuses it until the next day.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

DK_TZ = ZoneInfo("Europe/Copenhagen")

EDS_URL = "https://api.energidataservice.dk/dataset/DayAheadPrices"
PRICE_AREA = "DK2"
REFRESH_HOUR = 14

# Tariffs come from the DatahubPricelist dataset (DKK/kWh, excl. VAT,
# Price1..Price24 = hour 00-01 ... 23-24 local time).
TARIFF_URL = "https://api.energidataservice.dk/dataset/DatahubPricelist"
RADIUS_GLN = "5790000705689"      # Radius Elnet A/S - DSO for Copenhagen
ENERGINET_GLN = "5790000432752"   # Energinet - TSO
RADIUS_NET_TARIFF_CODE = "DT_C_01"  # "Nettarif C time" (household / small business)


def _cache_bucket() -> str:
    """A string that only changes once per day, right after 14:00.
    Passing this into the cached fetch function means Streamlit will
    only actually re-fetch when it changes - i.e. once daily."""
    now = datetime.now(DK_TZ)
    bucket_date = now.date() if now.hour >= REFRESH_HOUR else now.date() - timedelta(days=1)
    return bucket_date.isoformat()


@st.cache_data(show_spinner="Fetching latest prices...")
def fetch_prices(cache_bucket: str) -> pd.DataFrame:
    start = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    params = {
        "start": start,
        "end": end,
        "filter": f'{{"PriceArea":["{PRICE_AREA}"]}}',
        "sort": "TimeDK asc",
    }
    resp = requests.get(EDS_URL, params=params, timeout=30)
    resp.raise_for_status()
    records = resp.json().get("records", [])
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No data returned from Energi Data Service")
    df["HourDK"] = pd.to_datetime(df["TimeDK"]).dt.tz_localize(DK_TZ)
    df["SpotPriceDKK"] = df["DayAheadPriceDKK"]
    return df


@st.cache_data(ttl=6 * 3600, show_spinner="Fetching tariffs...")
def fetch_tariffs() -> pd.DataFrame:
    """All Radius + Energinet tariff records (D03 = tariffs). Tariffs are valid
    for months at a time, so we pull the latest records and filter by validity
    ourselves rather than using start/end (which key off ValidFrom)."""
    params = {
        "filter": f'{{"GLN_Number":["{RADIUS_GLN}","{ENERGINET_GLN}"],"ChargeType":["D03"]}}',
        "sort": "ValidFrom desc",
        "limit": 2000,
    }
    resp = requests.get(TARIFF_URL, params=params, timeout=30)
    resp.raise_for_status()
    df = pd.DataFrame(resp.json().get("records", []))
    if df.empty:
        raise ValueError("No tariff data returned from Energi Data Service")
    df["GLN_Number"] = df["GLN_Number"].astype(str)
    df["Note"] = df["Note"].fillna("").astype(str)
    df["ChargeTypeCode"] = df["ChargeTypeCode"].fillna("").astype(str)
    df["ValidFrom"] = pd.to_datetime(df["ValidFrom"], errors="coerce")
    df["ValidTo"] = pd.to_datetime(df["ValidTo"], errors="coerce")  # NaT = open-ended
    for h in range(1, 25):
        df[f"Price{h}"] = pd.to_numeric(df.get(f"Price{h}"), errors="coerce")
    return df


# component column -> (legend name, row selector on the tariff table)
TARIFF_COMPONENTS = {
    "DSO": (
        "DSO net tariff (Radius)",
        lambda t: (t["GLN_Number"] == RADIUS_GLN)
        & ((t["ChargeTypeCode"] == RADIUS_NET_TARIFF_CODE) | (t["Note"].str.lower() == "nettarif c time")),
    ),
    "TSOTransmission": (
        "TSO transmission tariff",
        lambda t: (t["GLN_Number"] == ENERGINET_GLN)
        & t["Note"].str.contains("transmission", case=False)
        & t["Note"].str.contains("net", case=False),
    ),
    "TSOSystem": (
        "TSO system tariff",
        lambda t: (t["GLN_Number"] == ENERGINET_GLN) & t["Note"].str.contains("systemtarif", case=False),
    ),
}


def tariff_ore_kwh(rows: pd.DataFrame, hours_dk: pd.Series) -> pd.Series:
    """øre/kWh for each timestamp, picking the record valid at that moment and
    the price for that local hour. Returns NaN where no record is valid."""
    local = hours_dk.dt.tz_localize(None)  # wall-clock Danish time
    hour = hours_dk.dt.hour
    out = pd.Series(np.nan, index=hours_dk.index)
    for _, r in rows.sort_values("ValidFrom").iterrows():  # later records overwrite earlier
        mask = local >= r["ValidFrom"]
        if pd.notna(r["ValidTo"]):
            mask &= local < r["ValidTo"]
        # PriceN for the hour; fall back to Price1 for flat tariffs that only fill it
        prices = hour.map(lambda h: r[f"Price{h + 1}"] if pd.notna(r[f"Price{h + 1}"]) else r["Price1"])
        out[mask] = prices[mask]
    return out * 100  # DKK/kWh -> øre/kWh


st.set_page_config(page_title="Electricity Prices", layout="wide")
st.title("⚡ Electricity Prices — DK2 (Copenhagen)")

df = fetch_prices(_cache_bucket())
area_df = df.sort_values("HourDK")

now = pd.Timestamp.now(tz=DK_TZ)
future = area_df[area_df["HourDK"] >= now].copy()

if not future.empty:
    future["Spot"] = future["SpotPriceDKK"] / 10  # DKK/MWh -> øre/kWh

    # --- tariffs (DSO + TSO); fall back to spot-only if unavailable -------------
    components = [("Spot", "Spot price")]
    matched = []
    try:
        tariffs = fetch_tariffs()
        missing = []
        for col, (label, selector) in TARIFF_COMPONENTS.items():
            rows = tariffs[selector(tariffs)]
            series = tariff_ore_kwh(rows, future["HourDK"]) if not rows.empty else None
            if series is None or series.isna().all():
                missing.append(label)
                continue
            future[col] = series.fillna(0)
            components.append((col, label))
            matched.append(rows)
        if missing:
            st.warning("No currently valid tariff found for: " + ", ".join(missing))
    except Exception as exc:  # network / schema problems shouldn't kill the price view
        st.warning(f"Could not load tariffs ({exc}). Showing spot price only.")

    has_tariffs = len(components) > 1
    include_tariffs = has_tariffs and st.toggle(
        "Include tariffs in current price and cheapest windows", value=True
    )
    tariff_cols = [c for c, _ in components if c != "Spot"]
    future["Total"] = future["Spot"] + (future[tariff_cols].sum(axis=1) if include_tariffs else 0)

    suffix = " (spot + tariffs)" if include_tariffs else " (spot only)"
    st.metric("Current price" + suffix, f"{future.iloc[0]['Total']:.1f} øre/kWh")

    st.subheader("Cheapest upcoming windows")
    rows = []
    for period_hours in range(1, 7):
        period_length = period_hours * 4  # each row is a 15-minute period
        if len(future) >= period_length:
            rolling_avg = future["Total"].rolling(window=period_length).mean()
            end_idx = rolling_avg.idxmin()
            end_pos = future.index.get_loc(end_idx)
            start_pos = end_pos - period_length + 1
            window = future.iloc[start_pos : end_pos + 1]
            window_start = window.iloc[0]["HourDK"]
            window_end = window.iloc[-1]["HourDK"] + pd.Timedelta(minutes=15)
            avg_price = window["Total"].mean()
            rows.append(
                {
                    "Period": f"{period_hours}h",
                    "Start": window_start.strftime("%a %H:%M"),
                    "End": window_end.strftime("%a %H:%M"),
                    "Avg price (øre/kWh)": round(avg_price, 1),
                }
            )
        else:
            rows.append({"Period": f"{period_hours}h", "Start": "-", "End": "-", "Avg price (øre/kWh)": None})

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    # Stacked bars: each 15-min bar starts at its timestamp and is 15 min wide.
    # barmode="relative" stacks positives upward and negatives (negative spot) downward.
    fig = go.Figure()
    for col, label in components:
        fig.add_trace(
            go.Bar(
                x=future["HourDK"],
                y=future[col],
                name=label,
                width=15 * 60 * 1000,
                offset=0,
                hovertemplate=f"{label}: %{{y:.1f}} øre/kWh<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="relative",
        bargap=0,
        yaxis_title="øre/kWh",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=0, r=0, t=10, b=0),
        height=400,
    )
    st.plotly_chart(fig, width="stretch")

    if matched:
        with st.expander("Tariff records in use"):
            used = pd.concat(matched)
            used = used[
                (used["ValidTo"].isna() | (used["ValidTo"] > now.tz_localize(None)))
                & (used["ValidFrom"] <= future["HourDK"].max().tz_localize(None))
            ]
            st.dataframe(
                used[["GLN_Number", "ChargeTypeCode", "Note", "ValidFrom", "ValidTo"]],
                hide_index=True,
                width="stretch",
            )

st.caption(
    "Prices in øre/kWh, excl. VAT and electricity tax (elafgift). Tariffs: Radius (DSO) and "
    "Energinet (TSO). Spot refreshes once daily shortly after 14:00."
)

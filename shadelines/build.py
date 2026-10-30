"""Put today's numbers on the 1936 map, then summarize by HOLC grade.

    python -m shadelines.build

Reads data/raw/, writes:
    data/processed/shadelines.gpkg   every HOLC area with today's measures (opens in QGIS / ArcGIS Pro)
    site/data/zones.geojson          the same, simplified for the web map
    site/data/summary.json           the by-grade table, the citywide baseline and data notes
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from shadelines import load
from shadelines.interpolate import areal_weighted, dasymetric

GRADES = ["A", "B", "C", "D"]
GRADE_NAMES = {"A": "Best", "B": "Still desirable", "C": "Declining", "D": "Hazardous"}
ACS_COUNTS = ["population", "race_total", "white_alone_not_hispanic", "occupied_units", "owner_occupied_units"]
ACS_MEANS = ["median_household_income"]


def measures(
    target: gpd.GeoDataFrame, tid: str, canopy: gpd.GeoDataFrame, acs: pd.DataFrame, blocks: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Canopy (by area) and ACS figures (through blocks) for each target polygon."""
    can = areal_weighted(canopy, target, "tract", tid, extensive=["canopy_2016", "canopy_2021", "land"])
    ppl = dasymetric(acs, blocks, target, "bg", "bg", "pop2020", tid, counts=ACS_COUNTS, weighted_means=ACS_MEANS)
    return can.merge(ppl, on=tid, how="outer")


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        df["canopy_pct_2016"] = 100 * df["canopy_2016"] / df["land"]
        df["canopy_pct_2021"] = 100 * df["canopy_2021"] / df["land"]
        df["canopy_change_pts"] = df["canopy_pct_2021"] - df["canopy_pct_2016"]
        df["people_of_color_pct"] = 100 * (1 - df["white_alone_not_hispanic"] / df["race_total"])
        df["owner_occupied_pct"] = 100 * df["owner_occupied_units"] / df["occupied_units"]
    # Rates over (almost) nobody are noise, not data.
    df.loc[df["race_total"] < 50, ["people_of_color_pct"]] = np.nan
    df.loc[df["occupied_units"] < 20, ["owner_occupied_pct"]] = np.nan
    return df


def summarize(zones: pd.DataFrame, group: str = "grade") -> pd.DataFrame:
    """Totals per group, then rates from the totals, so big areas count for more than small ones."""
    sums = zones.groupby(group)[["canopy_2016", "canopy_2021", "land", *ACS_COUNTS, "people", "area_km2"]].sum(
        min_count=1
    )
    sums["areas"] = zones.groupby(group).size()
    inc = zones.dropna(subset=["median_household_income"])
    sums["median_household_income"] = (inc["median_household_income"] * inc["people"]).groupby(inc[group]).sum() / inc[
        "people"
    ].groupby(inc[group]).sum()
    out = add_rates(sums)
    q = zones.groupby(group)["canopy_pct_2021"].quantile([0.25, 0.5, 0.75]).unstack()
    out["canopy_pct_2021_q1"], out["canopy_pct_2021_median_area"], out["canopy_pct_2021_q3"] = q[0.25], q[0.5], q[0.75]
    return out


def build(raw: Path = load.RAW, out_dir: Path = Path("data/processed"), site_dir: Path = Path("site/data")) -> dict:
    holc = load.holc(raw)
    canopy = load.canopy_tracts(raw)
    acs = load.acs_block_groups(raw)
    blocks = load.blocks(raw)
    city = load.city_limits(raw)

    zones = holc.merge(measures(holc, "area_id", canopy, acs, blocks), on="area_id", how="left")
    zones["area_km2"] = zones.area / 1e6
    zones = gpd.GeoDataFrame(add_rates(zones), geometry="geometry", crs=holc.crs)

    graded = zones[zones["grade"].isin(GRADES)]
    by_grade = summarize(graded).reindex(GRADES)

    city["_id"] = 1
    baseline = add_rates(measures(city, "_id", canopy, acs, blocks)).iloc[0]

    out_dir.mkdir(parents=True, exist_ok=True)
    site_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "shadelines.gpkg"
    gpkg.unlink(missing_ok=True)
    zones.to_file(gpkg, layer="holc_1936_with_2021_measures", driver="GPKG")
    city.drop(columns="_id").to_file(gpkg, layer="seattle_city_limits", driver="GPKG")
    (site_dir / "shadelines.gpkg").write_bytes(gpkg.read_bytes())

    web_cols = [
        "area_id", "grade", "label", "canopy_pct_2016", "canopy_pct_2021", "canopy_change_pts",
        "people_of_color_pct", "median_household_income", "owner_occupied_pct", "population", "area_km2",
    ]  # fmt: skip
    web = zones[web_cols + ["geometry"]].copy()
    web["geometry"] = web.geometry.simplify(3)  # metres; invisible at city zoom, halves the file
    web = web.to_crs(4326)
    for c in web_cols[3:]:
        web[c] = web[c].round(1 if c != "area_km2" else 3)
    (site_dir / "zones.geojson").write_text(web.to_json(drop_id=True, na="null"))
    city.drop(columns="_id").to_crs(4326).to_file(site_dir / "city.geojson", driver="GeoJSON")

    def row(r: pd.Series) -> dict:
        keys = [
            "areas", "area_km2", "population", "canopy_pct_2016", "canopy_pct_2021", "canopy_change_pts",
            "canopy_pct_2021_q1", "canopy_pct_2021_median_area", "canopy_pct_2021_q3",
            "people_of_color_pct", "median_household_income", "owner_occupied_pct",
        ]  # fmt: skip
        return {k: (None if k not in r or pd.isna(r[k]) else round(float(r[k]), 3)) for k in keys}

    manifest = json.loads((raw / "MANIFEST.json").read_text()) if (raw / "MANIFEST.json").exists() else {}
    summary = {
        "grades": {g: {"name": GRADE_NAMES[g], **row(by_grade.loc[g])} for g in GRADES},
        "city": row(baseline),
        "ungraded_areas": int((~zones["grade"].isin(GRADES)).sum()),
        "check": {
            # Our citywide canopy should match the city's own published figure closely.
            "city_canopy_pct_2021_from_tracts": round(float(baseline["canopy_pct_2021"]), 2),
        },
        "sources": {k: {"url": v["url"], "fetched_at": v["fetched_at"]} for k, v in manifest.items()},
    }
    (site_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    s = build()
    print(
        json.dumps(
            {g: {k: s["grades"][g][k] for k in ("areas", "canopy_pct_2021", "people_of_color_pct")} for g in GRADES}
            | {"city": s["city"]},
            indent=1,
        )
    )

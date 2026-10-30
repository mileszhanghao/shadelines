"""Read each raw file into a tidy GeoDataFrame or DataFrame with consistent ids and units."""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from shadelines.interpolate import CRS

RAW = Path("data/raw")

# The Census API marks unavailable estimates with large negative sentinels.
CENSUS_MISSING = [-666666666, -999999999, -888888888, -555555555, -333333333, -222222222]


def holc(raw: Path = RAW, city: str = "Seattle", state: str = "WA") -> gpd.GeoDataFrame:
    """The 1936 HOLC areas for one city."""
    path = next(p for p in (raw / "holc.gpkg", raw / "holc.json", raw / "holc.parquet") if p.exists())
    gdf = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
    gdf = gdf[(gdf["city"] == city) & (gdf["state"] == state)].copy()
    if gdf.empty:
        raise ValueError(f"no HOLC areas for {city}, {state} in {path}")
    gdf["grade"] = gdf["grade"].fillna("").str.strip().str.upper().replace("", "Ungraded")
    gdf["label"] = gdf["label"].fillna("").astype(str)
    gdf["area_id"] = gdf["area_id"].astype(int)
    keep = ["area_id", "grade", "label", "residential", "commercial", "industrial", "geometry"]
    gdf = gdf[[c for c in keep if c in gdf.columns]]
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf.to_crs(CRS).reset_index(drop=True)


CANOPY_FIELDS = ["TreeCanopy_2016_Area", "TreeCanopy_2021_Area", "Total_A", "Water_A", "TreeCanopy_2021_Percent"]


def _canopy(path: Path) -> gpd.GeoDataFrame | None:
    gdf = gpd.read_file(path)
    id_col = next((c for c in gdf.columns if c.upper().startswith("GEOID")), None)
    if id_col is None or not all(f in gdf.columns for f in CANOPY_FIELDS):
        return None
    out = gpd.GeoDataFrame(
        {
            "unit": gdf[id_col].astype(str),
            "canopy_2016": gdf["TreeCanopy_2016_Area"].astype(float),
            "canopy_2021": gdf["TreeCanopy_2021_Area"].astype(float),
            "land": (gdf["Total_A"].fillna(0) - gdf["Water_A"].fillna(0)).astype(float),
            "published_pct_2021": gdf["TreeCanopy_2021_Percent"].astype(float),
        },
        geometry=gdf.geometry.make_valid(),
        crs=gdf.crs,
    )
    return out.to_crs(CRS)


def canopy(raw: Path = RAW) -> tuple[gpd.GeoDataFrame, str]:
    """City of Seattle canopy assessment: canopy and land area in 2016 and 2021.

    Uses block groups when that layer has the needed fields (finer than tracts, so
    less smoothing when split between HOLC areas), else census tracts. Areas are
    kept as totals so they can be split between HOLC areas and added up;
    percentages are recomputed afterwards from the totals.
    """
    for name, level in (("canopy_block_groups.geojson", "block group"), ("canopy_tracts.geojson", "tract")):
        if (raw / name).exists() and (gdf := _canopy(raw / name)) is not None:
            return gdf, level
    raise ValueError("no canopy layer with the expected fields")


def acs_block_groups(raw: Path = RAW) -> pd.DataFrame:
    """ACS 5-year estimates per block group, with readable column names."""
    df = pd.read_csv(raw / "acs_block_groups.csv", dtype={"GEO_ID": str})
    df["bg"] = df["GEO_ID"].str.split("US").str[-1]
    for col in df.columns.drop(["GEO_ID", "bg"]):
        df[col] = pd.to_numeric(df[col], errors="coerce").replace(CENSUS_MISSING, np.nan)
    return df.drop(columns="GEO_ID")


def blocks(raw: Path = RAW) -> gpd.GeoDataFrame:
    """2020 census blocks with their 2020 population and parent block group."""
    gdf = gpd.read_file(raw / "blocks_2020.gpkg")
    gdf = gdf.rename(columns={"GEOID20": "geoid", "POP20": "pop2020"})[["geoid", "pop2020", "geometry"]]
    gdf["pop2020"] = pd.to_numeric(gdf["pop2020"]).fillna(0)
    gdf["bg"] = gdf["geoid"].astype(str).str[:12]
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf.to_crs(CRS)


def _read_zipped_shapefile(path: Path) -> gpd.GeoDataFrame:
    with zipfile.ZipFile(path) as z:
        shp = next(n for n in z.namelist() if n.endswith(".shp"))
    return gpd.read_file(f"zip://{path.resolve()}!{shp}")


def city_limits(raw: Path = RAW, name: str = "Seattle") -> gpd.GeoDataFrame:
    places = _read_zipped_shapefile(raw / "cb_place.zip")
    return places[places["NAME"] == name][["NAME", "geometry"]].to_crs(CRS).reset_index(drop=True)

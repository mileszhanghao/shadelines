"""Read each raw file into a tidy GeoDataFrame or DataFrame with consistent ids and units."""

from __future__ import annotations

import json
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


def canopy_tracts(raw: Path = RAW) -> gpd.GeoDataFrame:
    """City of Seattle canopy assessment by census tract: canopy and land area in 2016 and 2021.

    Areas are kept as totals so they can be split between HOLC areas and added up;
    percentages are recomputed afterwards from the totals.
    """
    gdf = gpd.read_file(raw / "canopy_tracts.geojson")
    out = gpd.GeoDataFrame(
        {
            "tract": gdf["GEOID10"].astype(str),
            "canopy_2016": gdf["TreeCanopy_2016_Area"].astype(float),
            "canopy_2021": gdf["TreeCanopy_2021_Area"].astype(float),
            "land": (gdf["Total_A"].fillna(0) - gdf["Water_A"].fillna(0)).astype(float),
            "published_pct_2021": gdf["TreeCanopy_2021_Percent"].astype(float),
        },
        geometry=gdf.geometry.make_valid(),
        crs=gdf.crs,
    )
    return out.to_crs(CRS)


def _census_json(path: Path) -> pd.DataFrame:
    rows = json.loads(path.read_text())
    return pd.DataFrame(rows[1:], columns=rows[0])


def acs_block_groups(raw: Path = RAW) -> pd.DataFrame:
    """ACS 5-year estimates per block group, with readable column names."""
    df = _census_json(raw / "acs_block_groups.json")
    names = json.loads((raw / "acs.variables.json").read_text())
    df["bg"] = df["state"] + df["county"] + df["tract"] + df["block group"]
    df = df.rename(columns=names)
    for col in names.values():
        df[col] = pd.to_numeric(df[col], errors="coerce").replace(CENSUS_MISSING, np.nan)
    return df[["bg", *names.values()]]


def blocks(raw: Path = RAW) -> gpd.GeoDataFrame:
    """2020 census blocks in Seattle with their 2020 population and parent block group."""
    pop = _census_json(raw / "blocks_population.json")
    pop["geoid"] = pop["state"] + pop["county"] + pop["tract"] + pop["block"]
    pop["pop2020"] = pd.to_numeric(pop["P1_001N"])
    gdf = gpd.read_file(raw / "blocks_2020.geojson")
    id_col = next(c for c in gdf.columns if c.upper() in ("GEOID20", "GEOID", "GEOID_20"))
    gdf = gdf.rename(columns={id_col: "geoid"})[["geoid", "geometry"]]
    gdf["geoid"] = gdf["geoid"].astype(str)
    gdf = gdf.merge(pop[["geoid", "pop2020"]], on="geoid", how="left")
    gdf["pop2020"] = gdf["pop2020"].fillna(0)
    gdf["bg"] = gdf["geoid"].str[:12]
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf.to_crs(CRS)


def _read_zipped_shapefile(path: Path) -> gpd.GeoDataFrame:
    with zipfile.ZipFile(path) as z:
        shp = next(n for n in z.namelist() if n.endswith(".shp"))
    return gpd.read_file(f"zip://{path.resolve()}!{shp}")


def city_limits(raw: Path = RAW, name: str = "Seattle") -> gpd.GeoDataFrame:
    places = _read_zipped_shapefile(raw / "cb_place.zip")
    return places[places["NAME"] == name][["NAME", "geometry"]].to_crs(CRS).reset_index(drop=True)

"""Package the inputs for redoing the analysis in ArcGIS Pro.

    python -m shadelines.arcgis_package     # after shadelines.fetch; writes site/data/arcgis/

Writes one GeoPackage in Washington State Plane North (EPSG:2926, US feet), the
projection Seattle's own GIS data uses, with every layer the ArcGIS workflow needs,
plus the Python results to check the ArcGIS numbers against.

Layers:
    holc_1936             HOLC areas (targets)
    canopy_block_groups   canopy and land area, 2016 and 2021 (square feet, to sum)
    acs_block_groups      ACS counts per block group, plus income x population
    block_points_2020     2020 census block centroids with population (weights)
    seattle_city_limits
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shadelines import load

CRS = "EPSG:2926"


def build(raw: Path = load.RAW, out_dir: Path = Path("site/data/arcgis")) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "shadelines_arcgis_inputs.gpkg"
    gpkg.unlink(missing_ok=True)

    city = load.city_limits(raw)
    holc = load.holc(raw)
    canopy, _ = load.canopy(raw)
    acs = load.acs_block_groups(raw)
    blocks = load.blocks(raw)

    # Keep only what lies in or near Seattle, so the file stays small.
    area = city.buffer(2000).union_all()
    canopy = canopy[canopy.intersects(area)]
    blocks = blocks[blocks.intersects(area)]

    with zipfile.ZipFile(raw / "cb_bg.zip") as z:
        shp = next(n for n in z.namelist() if n.endswith(".shp"))
    bg = gpd.read_file(f"zip://{(raw / 'cb_bg.zip').resolve()}!{shp}")[["GEOID", "geometry"]]
    bg = bg.rename(columns={"GEOID": "bg"}).merge(acs, on="bg").to_crs(holc.crs)
    bg = bg[bg.intersects(area)].copy()
    # A median can't be split between areas, but income x people can: sum both and divide.
    has_income = bg["median_household_income"].notna()
    bg["income_x_pop"] = (bg["median_household_income"] * bg["population"]).where(has_income, 0)
    bg["pop_with_income"] = bg["population"].where(has_income, 0)

    points = blocks[blocks["pop2020"] > 0][["geoid", "bg", "pop2020", "geometry"]].copy()
    points["geometry"] = points.representative_point()

    layers = {
        "holc_1936": holc,
        "canopy_block_groups": canopy.rename(columns={"unit": "bg_id"}),
        "acs_block_groups": bg,
        "block_points_2020": points,
        "seattle_city_limits": city,
    }
    for name, gdf in layers.items():
        gdf.to_crs(CRS).to_file(gpkg, layer=name, driver="GPKG")

    # The Python results, to compare against what ArcGIS produces.
    summary = json.loads(Path("site/data/summary.json").read_text())
    rows = [{"grade": g, **{k: v for k, v in summary["grades"][g].items() if k != "name"}} for g in "ABCD"]
    rows.append({"grade": "City", **summary["city"]})
    pd.DataFrame(rows).to_csv(out_dir / "python_results_by_grade.csv", index=False)
    return gpkg


if __name__ == "__main__":
    path = build()
    for name in gpd.list_layers(path)["name"]:
        print(name, len(gpd.read_file(path, layer=name)))
    print(path, f"{path.stat().st_size / 1e6:.1f} MB")

"""End to end on synthetic raw files shaped like the real downloads.

A 4 km x 1 km strip in Seattle split into four 1 km HOLC squares graded A..D (plus
one Tacoma area that must be ignored). Two census tracts each cover two squares:
the west tract has 40% canopy, the east 10%. One block group per square, each with
one populated block; incomes fall from A to D.
"""

import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import box

from shadelines import build
from shadelines.interpolate import CRS

X0, Y0 = 550_000, 5_270_000  # central Seattle in UTM 10N


def sq(i, w=1000):
    return box(X0 + i * w, Y0, X0 + (i + 1) * w, Y0 + 1000)


@pytest.fixture
def raw(tmp_path):
    r = tmp_path / "raw"
    r.mkdir()
    holc = gpd.GeoDataFrame(
        {
            "area_id": [1, 2, 3, 4, 99],
            "city": ["Seattle"] * 4 + ["Tacoma"],
            "state": ["WA"] * 5,
            "grade": ["A", "B", "C", "D", "A"],
            "label": ["A1", "B1", "C1", "D1", "A9"],
            "residential": [True] * 5,
            "commercial": [False] * 5,
            "industrial": [False] * 5,
        },
        geometry=[sq(0), sq(1), sq(2), sq(3), sq(10)],
        crs=CRS,
    ).to_crs(4326)
    holc.to_parquet(r / "holc.parquet")

    # Areas in square feet, as the city publishes them: only the ratio matters.
    ft2 = 1_000_000 * 2 * 10.7639
    tracts = gpd.GeoDataFrame(
        {
            "GEOID10": ["53033000100", "53033000200"],
            "TreeCanopy_2016_Area": [0.42 * ft2, 0.12 * ft2],
            "TreeCanopy_2021_Area": [0.40 * ft2, 0.10 * ft2],
            "Total_A": [ft2, ft2],
            "Water_A": [0.0, 0.0],
            "TreeCanopy_2021_Percent": [40.0, 10.0],
        },
        geometry=[box(X0, Y0, X0 + 2000, Y0 + 1000), box(X0 + 2000, Y0, X0 + 4000, Y0 + 1000)],
        crs=CRS,
    ).to_crs(4326)
    (r / "canopy_tracts.geojson").write_text(tracts.to_json())  # no block-group layer: falls back to tracts

    # ACS as extracted from the summary files: GEO_ID plus named columns.
    lines = [
        "GEO_ID,population,race_total,white_alone_not_hispanic,median_household_income,occupied_units,owner_occupied_units"
    ]
    for i, (white, income, owners) in enumerate(
        [(900, 150000, 80), (700, 110000, 60), (500, 70000, 40), (200, 40000, 20)]
    ):
        tract = "000100" if i < 2 else "000200"
        lines.append(f"1500000US53033{tract}{i + 1},1000,1000,{white},{income},400,{owners * 4}")
    lines.append("1500000US530330002009,0,0,0,-666666666,0,0")  # empty block group, income suppressed
    (r / "acs_block_groups.csv").write_text("\n".join(lines) + "\n")

    ids = [f"53033{'000100' if i < 2 else '000200'}{i + 1}001" for i in range(4)]
    gpd.GeoDataFrame({"GEOID20": ids, "POP20": [950] * 4}, geometry=[sq(i) for i in range(4)], crs=CRS).to_crs(
        4269
    ).to_file(r / "blocks_2020.gpkg", driver="GPKG")

    city = gpd.GeoDataFrame(
        {"NAME": ["Seattle", "Tacoma"]}, geometry=[box(X0, Y0, X0 + 4000, Y0 + 1000), sq(10)], crs=CRS
    )
    shp = tmp_path / "shp"
    shp.mkdir()
    city.to_crs(4269).to_file(shp / "cb_place.shp")
    with zipfile.ZipFile(r / "cb_place.zip", "w") as z:
        for f in shp.iterdir():
            z.write(f, f.name)
    return r


def test_build_end_to_end(raw, tmp_path):
    s = build.build(raw, tmp_path / "out", tmp_path / "site")
    g = s["grades"]

    assert [g[k]["areas"] for k in "ABCD"] == [1, 1, 1, 1]
    assert g["A"]["canopy_pct_2021"] == pytest.approx(40, abs=0.01)
    assert g["D"]["canopy_pct_2021"] == pytest.approx(10, abs=0.01)
    assert g["A"]["canopy_change_pts"] == pytest.approx(-2, abs=0.01)
    assert s["city"]["canopy_pct_2021"] == pytest.approx(25, abs=0.01)

    assert g["A"]["people_of_color_pct"] == pytest.approx(10, abs=0.01)
    assert g["D"]["people_of_color_pct"] == pytest.approx(80, abs=0.01)
    assert g["D"]["median_household_income"] == pytest.approx(40_000)
    assert g["A"]["owner_occupied_pct"] == pytest.approx(80)
    assert g["A"]["population"] == pytest.approx(1000)  # the ACS estimate, not the 2020 block count

    web = gpd.read_file(tmp_path / "site" / "zones.geojson")
    assert sorted(web["label"]) == ["A1", "B1", "C1", "D1"]  # Tacoma left out
    layers = gpd.list_layers(tmp_path / "out" / "shadelines.gpkg")["name"].tolist()
    assert "holc_1936_with_2021_measures" in layers
    assert (tmp_path / "site" / "shadelines.gpkg").exists()
    assert s["canopy_unit"] == "tract"


def test_figures_render(raw, tmp_path):
    from shadelines import figures

    build.build(raw, tmp_path / "out", tmp_path / "site")
    assert figures.maps(tmp_path / "out" / "shadelines.gpkg", tmp_path).stat().st_size > 10_000
    assert figures.by_grade(tmp_path / "site" / "summary.json", tmp_path).stat().st_size > 10_000

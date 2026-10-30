import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from shadelines.interpolate import CRS, areal_weighted, dasymetric


def gdf(rows, **cols):
    return gpd.GeoDataFrame(cols, geometry=rows, crs=CRS)


# Two 100 m x 100 m source squares side by side; one target covering the right half
# of the left square and all of the right square.
LEFT, RIGHT = box(0, 0, 100, 100), box(100, 0, 200, 100)
TARGET = box(50, 0, 200, 100)


def test_extensive_values_split_by_area():
    src = gdf([LEFT, RIGHT], sid=["L", "R"], canopy_m2=[4000.0, 1000.0])
    tgt = gdf([TARGET], tid=["T"])
    out = areal_weighted(src, tgt, "sid", "tid", extensive=["canopy_m2"])
    assert out.loc[0, "canopy_m2"] == pytest.approx(0.5 * 4000 + 1000)
    assert out.loc[0, "covered_area"] == pytest.approx(150 * 100)


def test_intensive_values_averaged_by_overlap_area():
    src = gdf([LEFT, RIGHT], sid=["L", "R"], pct=[40.0, 10.0])
    out = areal_weighted(src, gdf([TARGET], tid=["T"]), "sid", "tid", intensive=["pct"])
    # 50 m of LEFT at 40%, 100 m of RIGHT at 10%
    assert out.loc[0, "pct"] == pytest.approx((50 * 40 + 100 * 10) / 150)


def test_missing_values_are_left_out_of_the_average():
    src = gdf([LEFT, RIGHT], sid=["L", "R"], pct=[40.0, None])
    out = areal_weighted(src, gdf([TARGET], tid=["T"]), "sid", "tid", intensive=["pct"])
    assert out.loc[0, "pct"] == pytest.approx(40.0)


def test_reprojects_geographic_input():
    src = gdf([LEFT, RIGHT], sid=["L", "R"], pct=[40.0, 10.0]).to_crs(4326)
    tgt = gdf([TARGET], tid=["T"]).to_crs(4326)
    out = areal_weighted(src, tgt, "sid", "tid", intensive=["pct"])
    assert out.loc[0, "pct"] == pytest.approx(20.0, abs=0.01)


def test_dasymetric_follows_people_not_area():
    # One block group made of two blocks. All 100 residents live in the left block,
    # so a target covering only the right block gets nobody, even though it covers
    # half the block group's area.
    blocks = gdf([LEFT, RIGHT], bg=["BG1", "BG1"], pop20=[100, 0])
    acs = pd.DataFrame({"bg": ["BG1"], "population": [120.0], "income": [80_000.0]})
    targets = gdf([LEFT, RIGHT], tid=["west", "east"])
    out = dasymetric(acs, blocks, targets, "bg", "bg", "pop20", "tid", counts=["population"], weighted_means=["income"])
    out = out.set_index("tid")
    assert out.loc["west", "population"] == pytest.approx(120)  # the 2023 estimate, placed where people live
    assert out.loc["east", "population"] == pytest.approx(0)
    assert out.loc["west", "income"] == pytest.approx(80_000)
    assert pd.isna(out.loc["east", "income"])  # no residents, so no income to report


def test_dasymetric_weighted_mean_of_two_sources():
    blocks = gdf([LEFT, RIGHT], bg=["A", "B"], pop20=[300, 100])
    acs = pd.DataFrame({"bg": ["A", "B"], "income": [50_000.0, 150_000.0]})
    out = dasymetric(
        acs, blocks, gdf([box(0, 0, 200, 100)], tid=["all"]), "bg", "bg", "pop20", "tid", weighted_means=["income"]
    )
    assert out.loc[0, "income"] == pytest.approx((300 * 50_000 + 100 * 150_000) / 400)
    assert out.loc[0, "people"] == pytest.approx(400)


def test_block_split_by_target_boundary_shares_its_people_by_area():
    blocks = gdf([box(0, 0, 100, 100)], bg=["A"], pop20=[100])
    acs = pd.DataFrame({"bg": ["A"], "population": [100.0]})
    targets = gdf([box(0, 0, 25, 100), box(25, 0, 100, 100)], tid=["a", "b"])
    out = dasymetric(acs, blocks, targets, "bg", "bg", "pop20", "tid", counts=["population"]).set_index("tid")
    assert out.loc["a", "population"] == pytest.approx(25)
    assert out.loc["b", "population"] == pytest.approx(75)

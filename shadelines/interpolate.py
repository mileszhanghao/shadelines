"""Move numbers from one set of polygons (census tracts, block groups) onto another
(the 1936 HOLC areas) when their boundaries don't line up.

Two methods, used for different kinds of data:

* ``areal_weighted`` assumes a value is spread evenly over a polygon's area. That is
  the right assumption for land cover: a tract's tree canopy really is spread over its
  land, so a HOLC area covering 30% of a tract's land gets about 30% of its canopy.

* ``dasymetric`` is for people. Population is not spread evenly (a tract can be half
  park or rail yard), so counts are first pushed down to 2020 census blocks in
  proportion to where people actually live, then onto the target polygons. Blocks
  are small enough that most fall entirely inside one HOLC area.

Both work in a projected CRS with metre units; inputs are reprojected if needed.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

CRS = "EPSG:32610"  # UTM zone 10N, metres


def _projected(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf if gdf.crs is not None and gdf.crs.equals(CRS) else gdf.to_crs(CRS)


def overlay_weights(source: gpd.GeoDataFrame, target: gpd.GeoDataFrame, source_id: str, target_id: str) -> pd.DataFrame:
    """For every (source, target) pair that overlaps: the overlap area, and the share
    of the source polygon's area that it makes up."""
    s = _projected(source)[[source_id, "geometry"]].copy()
    t = _projected(target)[[target_id, "geometry"]].copy()
    s["source_area"] = s.area
    pieces = gpd.overlay(s, t, how="intersection", keep_geom_type=True)
    pieces["overlap_area"] = pieces.area
    pieces["share_of_source"] = pieces["overlap_area"] / pieces["source_area"]
    return pd.DataFrame(pieces[[source_id, target_id, "overlap_area", "share_of_source"]])


def areal_weighted(
    source: gpd.GeoDataFrame,
    target: gpd.GeoDataFrame,
    source_id: str,
    target_id: str,
    extensive: list[str] = (),
    intensive: list[str] = (),
) -> pd.DataFrame:
    """Interpolate by area.

    extensive: totals (an area of canopy, a count) are split by the share of the
        source polygon that falls in each target, then summed.
    intensive: rates and percentages are averaged, weighted by overlap area.

    Returns one row per target that overlaps any source, plus ``covered_area``:
    how much of the target the sources covered (to spot targets that fall partly
    outside the data, such as over water).
    """
    w = overlay_weights(source, target, source_id, target_id).merge(
        pd.DataFrame(source)[[source_id, *extensive, *intensive]], on=source_id
    )
    out = w.groupby(target_id).agg(covered_area=("overlap_area", "sum"))
    for col in extensive:
        out[col] = (w[col] * w["share_of_source"]).groupby(w[target_id]).sum(min_count=1)
    for col in intensive:
        valid = w[col].notna()
        num = (w.loc[valid, col] * w.loc[valid, "overlap_area"]).groupby(w.loc[valid, target_id]).sum()
        den = w.loc[valid, "overlap_area"].groupby(w.loc[valid, target_id]).sum()
        out[col] = num / den
    return out.reset_index()


def dasymetric(
    source: pd.DataFrame,
    blocks: gpd.GeoDataFrame,
    target: gpd.GeoDataFrame,
    source_id: str,
    block_source_id: str,
    block_pop: str,
    target_id: str,
    counts: list[str] = (),
    weighted_means: list[str] = (),
) -> pd.DataFrame:
    """Interpolate population-based data through census blocks.

    source: one row per source unit (e.g. ACS block group), no geometry needed.
    blocks: block polygons with a population column and the id of the source unit
        each block belongs to (``block_source_id``).
    counts: totals such as population, split by each block's share of its source
        unit's population, then by the block's area share in each target.
    weighted_means: values that can't be added up, such as a median income. Each
        target gets the mean of its sources' values, weighted by the people it
        received from each. This is an approximation and is labelled as such
        wherever it is shown.

    Returns one row per target with ``people``: the 2020 block population the target
    received (the weight behind every number in the row).
    """
    b = _projected(blocks)[[block_source_id, block_pop, "geometry"]].copy()
    b["_block"] = range(len(b))
    pop = b[block_pop].astype(float)
    totals = pop.groupby(b[block_source_id]).transform("sum")
    # A source unit whose blocks are all empty has nobody to give its counts to.
    b["share_of_source_people"] = (pop / totals).where(totals > 0, 0.0)

    w = overlay_weights(b, target, "_block", target_id).merge(
        b[["_block", block_source_id, block_pop, "share_of_source_people"]], on="_block"
    )
    w["people"] = w[block_pop].astype(float) * w["share_of_source"]
    w["share"] = w["share_of_source_people"] * w["share_of_source"]
    w = w.merge(
        pd.DataFrame(source)[[source_id, *counts, *weighted_means]], left_on=block_source_id, right_on=source_id
    )

    out = w.groupby(target_id).agg(people=("people", "sum"))
    for col in counts:
        out[col] = (w[col] * w["share"]).groupby(w[target_id]).sum(min_count=1)
    for col in weighted_means:
        valid = w[col].notna() & (w["people"] > 0)
        num = (w.loc[valid, col] * w.loc[valid, "people"]).groupby(w.loc[valid, target_id]).sum()
        den = w.loc[valid, "people"].groupby(w.loc[valid, target_id]).sum()
        out[col] = num / den
    return out.reset_index()

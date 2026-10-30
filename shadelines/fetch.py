"""Download the raw inputs into data/raw/ and record where each one came from.

Every file is listed in data/raw/MANIFEST.json with its URL, size, SHA-256 and the
time it was fetched, so a result can always be traced back to the exact bytes it
was computed from.

    python -m shadelines.fetch            # everything
    python -m shadelines.fetch holc acs   # only some sources
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

RAW = Path("data/raw")
UA = "shadelines/0.1 (portfolio project; https://github.com/mileszhanghao/shadelines)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA

# King County, Washington
STATE, COUNTY = "53", "033"
ACS_YEAR = 2023
SEATTLE_GIS = "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services"


@dataclass
class Fetched:
    name: str
    url: str
    path: Path


def _get(url: str, **params) -> requests.Response:
    for attempt in range(4):
        try:
            r = SESSION.get(url, params=params or None, timeout=120)
            if r.status_code < 500:
                r.raise_for_status()
                return r
        except requests.ConnectionError:
            if attempt == 3:
                raise
        time.sleep(2**attempt)
    r.raise_for_status()
    return r


def _save(name: str, url: str, content: bytes) -> Fetched:
    path = RAW / name
    path.write_bytes(content)
    return Fetched(name, url, path)


def holc() -> list[Fetched]:
    """HOLC ("redlining") area polygons from Mapping Inequality, all cities in one file."""
    candidates = [
        "https://dsl.richmond.edu/panorama/redlining/static/mappinginequality.gpkg",
        "https://dsl.richmond.edu/panorama/redlining/static/mappinginequality.json",
        # Cloud-native copy of the same data (GeoParquet), used if the lab's site is down.
        "https://data.source.coop/cboettig/mappinginequality/mappinginequality.parquet",
    ]
    errors = []
    for url in candidates:
        try:
            r = _get(url)
        except requests.RequestException as e:
            errors.append(f"{url}: {e}")
            continue
        return [_save("holc" + Path(url).suffix, url, r.content)]
    raise RuntimeError("no HOLC source reachable:\n" + "\n".join(errors))


def _arcgis_layer(service: str, layer: int, name: str) -> Fetched:
    """Download every feature of an ArcGIS FeatureServer layer as one GeoJSON file."""
    base = f"{SEATTLE_GIS}/{service}/FeatureServer/{layer}"
    meta = _get(base, f="json").json()
    (RAW / f"{name}.meta.json").write_text(json.dumps(meta, indent=1))
    page = meta.get("maxRecordCount", 1000)
    features: list[dict] = []
    while True:
        r = _get(
            f"{base}/query",
            where="1=1",
            outFields="*",
            outSR=4326,
            f="geojson",
            resultOffset=len(features),
            resultRecordCount=page,
            orderByFields=meta.get("objectIdField", "OBJECTID"),
        ).json()
        batch = r.get("features", [])
        features += batch
        if len(batch) < page and not r.get("exceededTransferLimit"):
            break
    fc = {"type": "FeatureCollection", "features": features}
    return _save(f"{name}.geojson", base, json.dumps(fc).encode())


def _first_layer(service: str) -> int:
    info = _get(f"{SEATTLE_GIS}/{service}/FeatureServer", f="json").json()
    return info["layers"][0]["id"]


def canopy() -> list[Fetched]:
    """City of Seattle 2016/2021 tree canopy assessment (LiDAR land cover), by block group and by tract."""
    listing = _get(SEATTLE_GIS, f="json").json()
    names = sorted(s["name"] for s in listing.get("services", []) if "canopy" in s["name"].lower())
    (RAW / "canopy.services.json").write_text(json.dumps(names, indent=1))
    bg = "Tree_Canopy_Block_Groups_2016_2021"
    return [
        _arcgis_layer(bg, _first_layer(bg), "canopy_block_groups"),
        _arcgis_layer("Seattle_Tree_Canopy_2016_2021_RSE_Census_Tracts", 37, "canopy_tracts"),
    ]


# ACS columns we keep, from the Census Bureau's table-based summary files.
ACS_COLUMNS = {
    "B01003_E001": "population",
    "B03002_E001": "race_total",
    "B03002_E003": "white_alone_not_hispanic",
    "B19013_E001": "median_household_income",
    "B25003_E001": "occupied_units",
    "B25003_E002": "owner_occupied_units",
}


def _stream_filtered(url: str, keep: Callable[[str], bool]) -> tuple[list[str], list[list[str]]]:
    """Download a large pipe-delimited file line by line, keeping the header and matching rows."""
    with SESSION.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"  # the server sends no charset, so iter_lines would yield bytes
        lines = r.iter_lines(decode_unicode=True)
        header = next(lines).split("|")
        rows = [line.split("|") for line in lines if line and keep(line)]
    return header, rows


def acs() -> list[Fetched]:
    """ACS 5-year estimates for King County block groups.

    Read from the bulk summary files rather than the Census API, which needs a key
    from shared CI machines. Each file covers one table for every US geography; only
    King County block groups (GEO_ID 1500000US53033...) are kept.
    """
    prefix = f"1500000US{STATE}{COUNTY}"
    base = f"https://www2.census.gov/programs-surveys/acs/summary_file/{ACS_YEAR}/table-based-SF/data/5YRData"
    table: dict[str, dict[str, str]] = {}
    urls = []
    for t in sorted({c.split("_")[0] for c in ACS_COLUMNS}):
        url = f"{base}/acsdt5y{ACS_YEAR}-{t.lower()}.dat"
        urls.append(url)
        header, rows = _stream_filtered(url, lambda line: line.startswith(prefix))
        idx = {c: header.index(c) for c in ACS_COLUMNS if c.startswith(t)}
        for row in rows:
            rec = table.setdefault(row[0], {"GEO_ID": row[0]})
            rec.update({ACS_COLUMNS[c]: row[i] for c, i in idx.items()})
    if not table:
        raise RuntimeError("no King County block groups found in the ACS summary files")
    cols = ["GEO_ID", *ACS_COLUMNS.values()]
    text = "\n".join([",".join(cols)] + [",".join(r.get(c, "") for c in cols) for r in table.values()]) + "\n"
    (RAW / "acs.columns.json").write_text(json.dumps(ACS_COLUMNS, indent=1))
    return [_save("acs_block_groups.csv", " ".join(urls), text.encode())]


def blocks() -> list[Fetched]:
    """2020 Census blocks in King County with their 2020 population (TIGER/Line POP20 field).

    Used to place people within block groups (dasymetric weights). The statewide file
    is ~100 MB; only King County is kept.
    """
    import tempfile

    import geopandas as gpd

    url = f"https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{STATE}_tabblock20.zip"
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "blocks.zip"
        with SESSION.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with zpath.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        gdf = gpd.read_file(zpath, columns=["GEOID20", "COUNTYFP20", "POP20"])
    gdf = gdf[gdf["COUNTYFP20"] == COUNTY].drop(columns="COUNTYFP20")
    if gdf.empty:
        raise RuntimeError(f"no blocks for county {COUNTY} in {url}")
    path = RAW / "blocks_2020.gpkg"
    path.unlink(missing_ok=True)
    gdf.to_file(path, driver="GPKG")
    return [Fetched("blocks_2020.gpkg", url, path)]


def tiger() -> list[Fetched]:
    """Census cartographic boundaries (shoreline-clipped): block groups, and places for the city limit."""
    base = f"https://www2.census.gov/geo/tiger/GENZ{ACS_YEAR}/shp"
    out = []
    for kind in ("bg", "place"):
        url = f"{base}/cb_{ACS_YEAR}_{STATE}_{kind}_500k.zip"
        out.append(_save(f"cb_{kind}.zip", url, _get(url).content))
    return out


def holc_tiles() -> list[Fetched]:
    """Check which URL pattern serves the georectified 1936 scan as map tiles (for the web map)."""
    z, x, y = 12, 656, 1430  # central Seattle
    patterns = [
        "https://s3.amazonaws.com/holc/tiles/WA/Seattle/1936/{z}/{x}/{y}.png",
        "https://dsl.richmond.edu/panorama/redlining/tiles/WA/Seattle/1936/{z}/{x}/{y}.png",
    ]
    report = {}
    for p in patterns:
        url = p.format(z=z, x=x, y=y)
        try:
            r = SESSION.get(url, timeout=30)
            report[p] = {"status": r.status_code, "type": r.headers.get("content-type"), "bytes": len(r.content)}
        except requests.RequestException as e:
            report[p] = {"error": str(e)}
    return [_save("holc_tiles.json", "probe", json.dumps(report, indent=1).encode())]


SOURCES: dict[str, Callable[[], list[Fetched]]] = {
    "holc": holc,
    "canopy": canopy,
    "acs": acs,
    "blocks": blocks,
    "tiger": tiger,
    "holc_tiles": holc_tiles,
}


def main(argv: list[str]) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    failed = []
    for key in argv or list(SOURCES):
        try:
            for f in SOURCES[key]():
                data = f.path.read_bytes()
                manifest[f.name] = {
                    "source": key,
                    "url": f.url,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                print(f"{key:10} {f.name:28} {len(data):>12,} bytes")
        except Exception as e:  # report every source, then fail
            failed.append(key)
            print(f"{key:10} FAILED: {e}", file=sys.stderr)
    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=1) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

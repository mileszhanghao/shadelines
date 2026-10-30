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


def canopy() -> list[Fetched]:
    """City of Seattle 2016/2021 tree canopy assessment (LiDAR land cover) by census tract."""
    listing = _get(SEATTLE_GIS, f="json").json()
    names = sorted(s["name"] for s in listing.get("services", []) if "canopy" in s["name"].lower())
    (RAW / "canopy.services.json").write_text(json.dumps(names, indent=1))
    return [_arcgis_layer("Seattle_Tree_Canopy_2016_2021_RSE_Census_Tracts", 37, "canopy_tracts")]


def _census(url: str, name: str, **params) -> Fetched:
    r = _get(url, **params)
    return _save(name, r.url, r.content)


def acs() -> list[Fetched]:
    """ACS 5-year estimates by block group: population, race and ethnicity, income."""
    variables = {
        "B01003_001E": "population",
        "B03002_001E": "race_total",
        "B03002_003E": "white_alone_not_hispanic",
        "B19013_001E": "median_household_income",
        "B25003_001E": "occupied_units",
        "B25003_002E": "owner_occupied_units",
    }
    (RAW / "acs.variables.json").write_text(json.dumps(variables, indent=1))
    return [
        _census(
            f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5",
            "acs_block_groups.json",
            get="NAME," + ",".join(variables),
            **{"for": "block group:*", "in": f"state:{STATE} county:{COUNTY} tract:*"},
        )
    ]


def blocks() -> list[Fetched]:
    """2020 Census block populations, used to place people within block groups (dasymetric weights)."""
    out = [
        _census(
            "https://api.census.gov/data/2020/dec/pl",
            "blocks_population.json",
            get="P1_001N",
            **{"for": "block:*", "in": f"state:{STATE} county:{COUNTY} tract:*"},
        )
    ]
    listing = _get(SEATTLE_GIS, f="json").json()
    names = [s["name"] for s in listing.get("services", []) if s["name"].startswith("2020_Census_Blocks")]
    if names:
        out.append(_arcgis_layer(names[0], 0, "blocks_2020"))
    return out


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

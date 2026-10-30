# Shadelines

**Where Seattle's 1936 redlining map drew red lines, is there shade today?** In 1936 the federal Home Owners' Loan Corporation (HOLC) graded Seattle's neighborhoods from A ("best") to D ("hazardous"), largely by who lived there. Shadelines lays the city's 2021 tree canopy survey and today's census figures over those 1936 boundaries.

**Interactive map:** https://mileszhanghao.github.io/shadelines/ (switch between the 1936 grades, canopy, canopy change, people of color and income; click any area; overlay the original 1936 map)

**Stack:** Python · GeoPandas · Shapely · pyogrio · Matplotlib · MapLibre GL · Observable Plot · GitHub Actions · GeoPackage output for QGIS and ArcGIS Pro

![Seattle's 1936 HOLC grades next to 2021 tree canopy in the same areas](https://mileszhanghao.github.io/shadelines/figures/maps.png)

## What it shows

60 graded areas (12 A, 19 B, 18 C, 11 D), all residential in the 1936 survey:

| 1936 grade | Tree canopy 2021 | Change since 2016 | People of color | Household income* | Owner-occupied homes |
|---|---|---|---|---|---|
| A · "Best" | 39% | −0.5 pts | 22% | $209k | 83% |
| B · "Still desirable" | 31% | 0.0 pts | 30% | $148k | 52% |
| C · "Declining" | 27% | −0.5 pts | 46% | $125k | 44% |
| D · "Hazardous" | 27% | −1.1 pts | 52% | $117k | 43% |
| Seattle today | 28% | −0.5 pts | 40% | $134k | 44% |

\*People-weighted average of block-group medians; an approximation (see below).

![Canopy, people of color and income by 1936 grade](https://mileszhanghao.github.io/shadelines/figures/by_grade.png)

- **The canopy gap is at the top, not the bottom.** Areas graded A have 39% canopy, almost 1.5 times the 27% of C and D areas. B areas sit between. C and D are about the same as each other and as the city average.
- **D areas lost the most canopy between 2016 and 2021**, more than twice the citywide rate.
- **The people line up more strongly than the trees.** Going from A to D, the share of residents who are people of color more than doubles, income falls by almost half, and homeownership halves. These are the 1936 boundaries, measured with 2019–2023 data.
- **D areas vary a lot**, from 13% to 39% canopy. The map shows which ones.

This is a description, not a measure of cause. The grades and today's numbers line up, but a map can't separate the effect of the grades from everything since: highways, urban renewal, zoning, parks, decades of investment and disinvestment.

## Method

**Data**

| What | Source | Unit |
|---|---|---|
| 1936 HOLC areas | [Mapping Inequality](https://dsl.richmond.edu/panorama/redlining/), University of Richmond | polygons |
| Tree canopy 2016 and 2021 | City of Seattle canopy assessment (LiDAR and imagery; University of Vermont Spatial Analysis Laboratory) | block groups |
| Population, race and ethnicity, income, tenure | Census ACS 5-year 2019–2023, table-based summary files | block groups |
| Where people live within block groups | 2020 Census blocks (TIGER/Line, `POP20`) | blocks |
| City limits | Census cartographic boundaries 2023 | place |

**Two ways of moving numbers onto 1936 boundaries** (`shadelines/interpolate.py`)

- **Canopy, by area.** Land cover really is spread over land, so each block group's canopy and land area are split between HOLC areas in proportion to the share of the block group they cover, then added up. Canopy % is computed from those totals. Splitting totals and recomputing the percentage, rather than averaging percentages, keeps big areas from being outvoted by small ones.
- **People, through census blocks** (dasymetric weighting). People don't spread evenly over a block group that may be half park or rail yard. Each block group's ACS figures are first placed onto its 2020 census blocks in proportion to where people were counted, then onto HOLC areas. Blocks are small enough that most fall entirely inside one area. Median income can't be added up, so an area's figure is the average of its block groups' medians, weighted by the people it received from each.

**Checks**

- Citywide canopy computed this way is 28.7% in 2016 and 28.2% in 2021. The city's [published figures](https://www.seattle.gov/trees/management/canopy-cover) are 28.6% and 28.1%.
- Citywide population comes to about 741,000 (ACS 2019–2023). The 2020 Census counted 737,015.
- The block-group canopy percentages reproduce the city's published ones exactly from area ÷ (total − water), which confirms how the city defines land area.
- One D area (D1) is mostly outside today's shoreline-clipped city limits: only 10% of it is covered by the canopy survey. Its canopy is left blank rather than estimated from that sliver. Its census figures are kept.
- `tests/` builds the whole pipeline on small synthetic files with known answers. The interpolation functions are tested on their own, including reprojection, missing values and a block split between two areas.

## Choices and trade-offs

- **Python instead of a desktop GIS.** I used ArcGIS Pro and QGIS in my UW geography courses. Here every step from download to web map is a script, so anyone can re-run it and get the same result, and CI rebuilds it when the data changes. The output is also a GeoPackage (`shadelines.gpkg`, downloadable from the map page) for anyone who wants to keep working in QGIS or ArcGIS Pro.
- **Block groups, not the canopy raster.** The city publishes canopy summarized by block group. Zonal statistics on the 1-metre land-cover raster would be exact for each 1936 area, but the raster is far larger than the rest of the inputs together. A median block group is about a third the size of a median HOLC area (0.35 vs 0.91 km²), so the areal split loses little. The tract-level layer is kept as a fallback in the code.
- **ACS 5-year estimates at block-group level** have wide margins of error. Area figures built from several block groups are more stable than any one block group, but small areas (four A areas are under 0.25 km²) should be read as rough. The margins of error aren't carried through, which would be the next improvement.
- **Census bulk files instead of the Census API.** The API now needs a key when called from shared CI machines. The bulk summary files need none and give the same estimates; the fetch step streams them and keeps only King County.
- **Fixed 1936 boundaries.** Some areas no longer exist as neighborhoods; parts are now freeways or parks. That is part of the story, but it means an area's "today" can include land very different from what was graded.

## Running it

```bash
pip install -e '.[dev]'
python -m shadelines.fetch          # download everything into data/raw (with a manifest of URLs and checksums)
python -m shadelines.build          # data/processed/shadelines.gpkg, site/data/*
python -m shadelines.figures        # site/figures/*.png
python -m http.server -d site       # map on :8000
pytest
```

In CI, *Fetch raw data* runs monthly and stores the downloaded snapshot on the `raw-data` branch, with a manifest of each file's source URL, size, SHA-256 and download time. *Build and publish* rebuilds the analysis from that snapshot and deploys the map.

## Background

This follows up on my earlier [Seattle tree canopy equity screener](https://github.com/mileszhanghao/geospatial-data-portfolio/tree/main/projects/seattle-tree-canopy-equity), which mapped canopy change by census tract. Shadelines adds the history: the 1936 boundaries, finer canopy data, population-weighted interpolation, and an interactive map.

## Credits and licenses

- HOLC areas and the georectified 1936 map: Robert K. Nelson, LaDale Winling, et al., "Mapping Inequality: Redlining in New Deal America," *American Panorama*, ed. Robert K. Nelson and Edward L. Ayers, University of Richmond Digital Scholarship Lab. CC BY-NC-SA 4.0. The derived data in this project is shared under the same license.
- Tree canopy: City of Seattle Office of Sustainability & Environment; data created by the University of Vermont Spatial Analysis Laboratory.
- Census data: U.S. Census Bureau, American Community Survey 2019–2023 and 2020 Census.
- Basemap: © OpenFreeMap, © OpenStreetMap contributors.

Code: MIT. Built by Miles Zhang. Developed with AI pair-programming assistance (Claude).

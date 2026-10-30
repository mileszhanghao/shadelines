"""Shadelines in ArcGIS Pro: the same analysis as the Python pipeline, with arcpy.

How to run (ArcGIS Pro 3.x, Basic license or higher):
  1. Download shadelines_arcgis_inputs.gpkg (see the README) and set GPKG below.
  2. In ArcGIS Pro, open or create a project, then View -> Python window
     (or Analysis -> Python -> Python Notebook).
  3. Paste this whole file and press Enter. Each step prints what it did.

What it does:
  1. Copies the GeoPackage layers into a file geodatabase next to the GeoPackage.
  2. Tree canopy -> 1936 HOLC areas by area (Apportion Polygon, AREA).
  3. ACS counts -> HOLC areas through 2020 census block points weighted by
     population (Apportion Polygon, POINTS + weight): dasymetric weighting.
  4. Leaves canopy blank where the city's survey covers under half of an area
     (Tabulate Intersection), as the Python version does.
  5. Rates per area and per grade (totals first, then rates).
  6. Writes the per-grade results to CSV and prints them next to the Python numbers.
  7. If run inside ArcGIS Pro, adds the result to the map, colors it by grade and
     by canopy, and exports a two-map layout as PDF and PNG.
"""

import csv
import os

import arcpy

# ---- Settings ---------------------------------------------------------------
GPKG = r"C:\Users\YOU\Downloads\shadelines_arcgis_inputs.gpkg"  # <- change this
MIN_CANOPY_COVERAGE = 50  # percent of an area the canopy survey must cover
# ------------------------------------------------------------------------------

# Python pipeline results, for comparison (people of color %, canopy %, income $).
PYTHON = {
    "A": {"canopy_pct_2021": 39.4, "poc_pct": 22.0, "income": 208984},
    "B": {"canopy_pct_2021": 30.5, "poc_pct": 29.8, "income": 148238},
    "C": {"canopy_pct_2021": 26.8, "poc_pct": 45.7, "income": 125395},
    "D": {"canopy_pct_2021": 27.1, "poc_pct": 51.6, "income": 116698},
}
GRADE_COLORS = {"A": (118, 168, 101), "B": (124, 181, 189), "C": (228, 203, 63), "D": (217, 131, 141)}

arcpy.env.overwriteOutput = True
folder = os.path.dirname(GPKG)
gdb = os.path.join(folder, "shadelines.gdb")


def step(n, text):
    print(f"\n[{n}] {text}")


def find_field(table, name):
    """Apportion Polygon may rename an output field; find it by its input name."""
    names = [f.name for f in arcpy.ListFields(table)]
    for candidate in names:
        if candidate.lower() == name.lower():
            return candidate
    for candidate in names:
        if candidate.lower().startswith(name.lower()):
            return candidate
    raise RuntimeError(f"field {name} not found in {table}; fields are {names}")


step(1, "Copy the GeoPackage layers into a file geodatabase")
if not os.path.exists(GPKG):
    raise SystemExit(f"GeoPackage not found: {GPKG}  (edit GPKG at the top of the script)")
if not arcpy.Exists(gdb):
    arcpy.management.CreateFileGDB(folder, "shadelines.gdb")
layers = ["holc_1936", "canopy_block_groups", "acs_block_groups", "block_points_2020", "seattle_city_limits"]
for layer in layers:
    arcpy.management.CopyFeatures(os.path.join(GPKG, f"main.{layer}"), os.path.join(gdb, layer))
    print(f"    {layer}: {arcpy.management.GetCount(os.path.join(gdb, layer))[0]} features")
arcpy.env.workspace = gdb

step(2, "Tree canopy onto 1936 areas, by area")
arcpy.analysis.ApportionPolygon(
    "canopy_block_groups",
    "canopy_2016 SUM;canopy_2021 SUM;land SUM",
    "holc_1936",
    "holc_canopy",
    "AREA",
)
print("    -> holc_canopy")

step(3, "ACS counts onto 1936 areas, weighted by 2020 block population")
people = [
    "population",
    "race_total",
    "white_alone_not_hispanic",
    "occupied_units",
    "owner_occupied_units",
    "income_x_pop",
    "pop_with_income",
]
arcpy.analysis.ApportionPolygon(
    "acs_block_groups",
    ";".join(f"{f} SUM" for f in people),
    "holc_canopy",
    "holc_measures",
    "POINTS",
    "block_points_2020",
    "pop2020",
)
print("    -> holc_measures")

F = {f: find_field("holc_measures", f) for f in ["canopy_2016", "canopy_2021", "land", *people]}

step(4, f"Blank canopy where the survey covers under {MIN_CANOPY_COVERAGE}% of an area")
arcpy.analysis.PairwiseDissolve("canopy_block_groups", "canopy_footprint")
arcpy.analysis.TabulateIntersection("holc_measures", "area_id", "canopy_footprint", "canopy_coverage")
coverage = {row[0]: row[1] for row in arcpy.da.SearchCursor("canopy_coverage", ["area_id", "PERCENTAGE"])}
blanked = []
with arcpy.da.UpdateCursor(
    "holc_measures", ["area_id", "label", F["canopy_2016"], F["canopy_2021"], F["land"]]
) as rows:
    for row in rows:
        if coverage.get(row[0], 0) < MIN_CANOPY_COVERAGE:
            blanked.append(row[1])
            rows.updateRow([row[0], row[1], None, None, None])
print(f"    blanked: {', '.join(blanked) or 'none'}")

step(5, "Rates for each area")
CODE = """
def ratio(a, b, scale=1.0):
    if a is None or b in (None, 0):
        return None
    return scale * a / b

def change(c2021, c2016, land):
    if c2021 is None or c2016 is None:
        return None
    return ratio(c2021 - c2016, land, 100)

def share_not(part, total):
    r = ratio(part, total, 100)
    return None if r is None else 100 - r
"""
c16, c21, land = F["canopy_2016"], F["canopy_2021"], F["land"]
rates = {
    "canopy_pct_2021": f"ratio(!{c21}!, !{land}!, 100)",
    "canopy_change_pts": f"change(!{c21}!, !{c16}!, !{land}!)",
    "poc_pct": f"share_not(!{F['white_alone_not_hispanic']}!, !{F['race_total']}!)",
    "income": f"ratio(!{F['income_x_pop']}!, !{F['pop_with_income']}!)",
    "owner_pct": f"ratio(!{F['owner_occupied_units']}!, !{F['occupied_units']}!, 100)",
}
for field, expression in rates.items():
    arcpy.management.CalculateField("holc_measures", field, expression, "PYTHON3", CODE, "DOUBLE")
    print(f"    {field}")

step(6, "Totals per grade, then rates (big areas count for more than small ones)")
sums = [
    "canopy_2021",
    "land",
    "race_total",
    "white_alone_not_hispanic",
    "income_x_pop",
    "pop_with_income",
    "occupied_units",
    "owner_occupied_units",
    "population",
]
totals = {}
with arcpy.da.SearchCursor("holc_measures", ["grade"] + [F[s] for s in sums]) as rows:
    for grade, *values in rows:
        if grade not in PYTHON:
            continue
        t = totals.setdefault(grade, {s: 0.0 for s in sums})
        for s, v in zip(sums, values):
            t[s] += v or 0.0

results = []
for grade in "ABCD":
    t = totals[grade]
    results.append(
        {
            "grade": grade,
            "canopy_pct_2021": round(100 * t["canopy_2021"] / t["land"], 1),
            "poc_pct": round(100 * (1 - t["white_alone_not_hispanic"] / t["race_total"]), 1),
            "income": round(t["income_x_pop"] / t["pop_with_income"]),
            "owner_pct": round(100 * t["owner_occupied_units"] / t["occupied_units"], 1),
            "population": round(t["population"]),
        }
    )

out_csv = os.path.join(folder, "arcgis_results_by_grade.csv")
with open(out_csv, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(results[0]))
    writer.writeheader()
    writer.writerows(results)

print(f"\n    {'grade':5} {'canopy % (ArcGIS / Python)':28} {'people of color %':22} {'income $':22}")
for r in results:
    p = PYTHON[r["grade"]]
    print(
        f"    {r['grade']:5} {r['canopy_pct_2021']:>6} / {p['canopy_pct_2021']:<19} "
        f"{r['poc_pct']:>6} / {p['poc_pct']:<13} {r['income']:>8,} / {p['income']:,}"
    )
print(f"\n    saved {out_csv}")
print("    Expect canopy to match within 0.1 and the census figures within about 0.5 points")
print("    (income within about 2%): ArcGIS gives each census block wholly to the area its")
print("    point falls in; the Python version splits blocks by area.")

step(7, "Map and layout (only inside ArcGIS Pro)")
try:
    aprx = arcpy.mp.ArcGISProject("CURRENT")
except Exception:
    aprx = None
    print("    Not running inside ArcGIS Pro: skipped. The results are in shadelines.gdb.")

if aprx is not None:
    try:
        m = aprx.activeMap or aprx.listMaps()[0]
        grade_map = aprx.createMap("1936 HOLC grades")
        canopy_map = aprx.createMap("Tree canopy 2021")
        for target in (grade_map, canopy_map):
            target.addBasemap("Light Gray Canvas")
            target.addDataFromPath(os.path.join(gdb, "seattle_city_limits"))
            target.addDataFromPath(os.path.join(gdb, "holc_measures"))

        # Grades: one color per grade.
        lyr = grade_map.listLayers("holc_measures")[0]
        sym = lyr.symbology
        sym.updateRenderer("UniqueValueRenderer")
        sym.renderer.fields = ["grade"]
        for group in sym.renderer.groups:
            for item in group.items:
                rgb = GRADE_COLORS.get(item.values[0][0])
                if rgb:
                    item.symbol.color = {"RGB": [*rgb, 100]}
        lyr.symbology = sym

        # Canopy: five classes, 10/20/30/40 percent.
        lyr = canopy_map.listLayers("holc_measures")[0]
        sym = lyr.symbology
        sym.updateRenderer("GraduatedColorsRenderer")
        sym.renderer.classificationField = "canopy_pct_2021"
        sym.renderer.breakCount = 5
        greens = [(243, 236, 205), (199, 217, 160), (141, 187, 111), (79, 143, 69), (33, 95, 44)]
        for brk, upper, rgb in zip(sym.renderer.classBreaks, [10, 20, 30, 40, 100], greens):
            brk.upperBound = upper
            brk.symbol.color = {"RGB": [*rgb, 100]}
        lyr.symbology = sym

        layout = aprx.createLayout(11, 8.5, "INCH", "Shadelines")
        extent = grade_map.listLayers("holc_measures")[0].getExtent()
        frames = []
        for target, x0 in ((grade_map, 0.3), (canopy_map, 5.7)):
            frame = layout.createMapFrame(arcpy.Extent(x0, 1.0, x0 + 5.0, 7.6), target, target.name)
            frame.camera.setExtent(extent)
            frames.append(frame)
            layout.createMapSurroundElement(arcpy.Point(x0 + 0.1, 1.1), "LEGEND", frame, name=f"{target.name} legend")
        layout.createMapSurroundElement(arcpy.Point(5.3, 0.55), "SCALE_BAR", frames[0])
        layout.createMapSurroundElement(arcpy.Point(10.4, 7.3), "NORTH_ARROW", frames[0])
        aprx.createTextElement(
            layout, arcpy.Point(0.3, 8.1), "POINT", "Seattle's 1936 HOLC grades and 2021 tree canopy", 20
        )
        aprx.createTextElement(
            layout,
            arcpy.Point(0.3, 0.45),
            "POINT",
            "HOLC areas: Mapping Inequality, CC BY-NC-SA 4.0. Canopy: City of Seattle 2021 assessment. "
            "Population: U.S. Census Bureau. Analysis: Miles Zhang, ArcGIS Pro.",
            8,
        )
        layout.exportToPDF(os.path.join(folder, "shadelines_arcgis_layout.pdf"))
        layout.exportToPNG(os.path.join(folder, "shadelines_arcgis_layout.png"), resolution=300)
        print(f"    layout exported to {folder}")
    except Exception as e:  # layout APIs differ between Pro versions
        print(f"    Layout step stopped: {e}")
        print("    The analysis results are done; build the layout by hand (guide step 6).")

print("\nDone.")

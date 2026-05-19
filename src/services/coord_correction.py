"""
Coordinate Correction Pipeline
================================
Applies 7 sequential fixes to raw "lat,lon" strings from the Excel file,
then closes the 4-point ring into a valid GeoJSON polygon.

Steps:
  1. Strip all whitespace (including internal spaces like "17. 504503")
  2. Validate comma separator exists
  3. Auto-inject decimal point if a component has no dot and >2 digits
  4. Cast to float — bail on any parse error
  5. Maharashtra bounds check: lat 15.6–22.1 / lon 72.6–80.9
  6. India lat/lon swap: if values are reversed, swap them back
  7. ConvexHull to close the 4-corner ring into a valid closed polygon
"""

import logging
from typing import Optional
from shapely.geometry import MultiPoint, mapping

logger = logging.getLogger(__name__)

# Maharashtra bounding box (generous, covers neighbouring districts)
LAT_MIN, LAT_MAX = 15.0, 22.5
LON_MIN, LON_MAX = 72.0, 81.5


def _inject_decimal(s: str) -> str:
    """If string has no decimal point and is longer than 2 chars, insert after pos 2."""
    if "." not in s and len(s) > 2:
        return s[:2] + "." + s[2:]
    return s


def clean_coord_string(raw: str) -> Optional[tuple[float, float]]:
    """
    Parse one raw "lat,lon" cell from the Excel sheet.
    Returns (lat, lon) as floats, or None if unfixable.
    Records each correction applied for telemetry.
    """
    if not raw or str(raw).strip() in ("nan", "None", ""):
        return None

    raw = str(raw)
    telemetry = []

    # Step 1 — strip all spaces (handles "16.4418, 74.1393" and "17. 504503, ...")
    cleaned = raw.replace(" ", "")
    if cleaned != raw:
        telemetry.append(f"stripped spaces: '{raw}' → '{cleaned}'")

    # Step 2 — must contain exactly one comma
    if cleaned.count(",") != 1:
        logger.warning("Cannot parse coord (no single comma): %s", raw)
        return None

    part_a, part_b = cleaned.split(",")

    # Step 3 — auto-inject decimal
    fixed_a = _inject_decimal(part_a)
    fixed_b = _inject_decimal(part_b)
    if fixed_a != part_a:
        telemetry.append(f"decimal injected in first component: '{part_a}' → '{fixed_a}'")
    if fixed_b != part_b:
        telemetry.append(f"decimal injected in second component: '{part_b}' → '{fixed_b}'")

    # Step 4 — cast to float
    try:
        v1 = float(fixed_a)
        v2 = float(fixed_b)
    except ValueError as exc:
        logger.warning("Float cast failed for '%s': %s", raw, exc)
        return None

    # Step 5 + 6 — bounds check and lat/lon swap
    def in_lat_range(v): return LAT_MIN <= v <= LAT_MAX
    def in_lon_range(v): return LON_MIN <= v <= LON_MAX

    if in_lat_range(v1) and in_lon_range(v2):
        lat, lon = v1, v2  # already correct
    elif in_lon_range(v1) and in_lat_range(v2):
        lat, lon = v2, v1  # swapped — fix it
        telemetry.append(f"lat/lon swapped: ({v1},{v2}) → lat={lat}, lon={lon}")
    elif in_lat_range(v1) and not in_lon_range(v2):
        # lon out of range — likely a bad decimal injection; best-effort
        logger.warning("Lon out of Maharashtra range after correction: %s → %.6f", raw, v2)
        lat, lon = v1, v2
    else:
        logger.warning("Both components out of Maharashtra range, skipping: %s", raw)
        return None

    if telemetry:
        logger.debug("Coord corrections for '%s': %s", raw, " | ".join(telemetry))

    return (lat, lon)


def build_closed_ring(raw_coords: list[str]) -> Optional[dict]:
    """
    Takes the 4 raw coord strings (A, B, C, D) from one farmer row.
    Returns a GeoJSON-ready dict:
      {
        "geojson": { "type": "Polygon", "coordinates": [[[lon,lat], ...]] },
        "area_ha": float,
        "corrections": [ ... telemetry strings ]
      }
    Or None if fewer than 3 valid points.
    """
    corrections = []
    points_latlon = []

    for i, raw in enumerate(raw_coords):
        result = clean_coord_string(raw)
        if result is None:
            corrections.append(f"Point {chr(65+i)} ('{raw}') could not be parsed — skipped")
            continue
        points_latlon.append(result)

    if len(points_latlon) < 3:
        logger.error("Fewer than 3 valid points — cannot form polygon")
        return None

    # Step 7 — ConvexHull closes the ring (handles out-of-order corners too)
    # Shapely uses (x=lon, y=lat)
    shapely_pts = [(lon, lat) for lat, lon in points_latlon]
    hull = MultiPoint(shapely_pts).convex_hull

    if hull.geom_type == "Point" or hull.geom_type == "LineString":
        logger.error("ConvexHull degenerate — all points collinear or identical")
        return None

    # exterior.coords is already closed (first == last)
    ring_lonlat = list(hull.exterior.coords)  # [(lon, lat), ...]

    geojson_coords = [list(ring_lonlat)]  # GeoJSON: [[[lon,lat], ...]]

    # Compute area in hectares using local UTM projection
    area_ha = _area_ha(hull)

    corrections.append(f"ConvexHull applied to {len(points_latlon)} valid points → "
                       f"closed ring with {len(ring_lonlat)} vertices")

    return {
        "geojson": {
            "type": "Polygon",
            "coordinates": geojson_coords,
        },
        "area_ha": round(area_ha, 3),
        "corrections": corrections,
    }


def parse_kml_polygon(kml_text: str) -> Optional[dict]:
    """
    Parse a KML string and extract the first Polygon's LinearRing.
    KML coordinates are "lon,lat,alt" space-separated.
    Returns same structure as build_closed_ring.
    """
    from lxml import etree

    try:
        root = etree.fromstring(kml_text.encode())
    except etree.XMLSyntaxError as exc:
        logger.error("KML parse error: %s", exc)
        return None

    ns = {"k": "http://www.opengis.net/kml/2.2"}
    coord_el = root.find(".//k:coordinates", ns)
    if coord_el is None:
        # Try without namespace
        coord_el = root.find(".//coordinates")
    if coord_el is None:
        logger.error("No <coordinates> element found in KML")
        return None

    raw_text = coord_el.text.strip()
    ring_lonlat = []
    for token in raw_text.split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
            ring_lonlat.append((lon, lat))
        except ValueError:
            continue

    if len(ring_lonlat) < 3:
        return None

    # KML rings are already closed (first == last) — deduplicate
    if ring_lonlat[0] == ring_lonlat[-1]:
        ring_lonlat = ring_lonlat[:-1]

    # Re-close via ConvexHull to ensure valid winding and handle any issues
    hull = MultiPoint(ring_lonlat).convex_hull
    closed = list(hull.exterior.coords)
    area_ha = _area_ha(hull)

    return {
        "geojson": {
            "type": "Polygon",
            "coordinates": [closed],
        },
        "area_ha": round(area_ha, 3),
        "corrections": [f"Parsed from KML — {len(ring_lonlat)} vertices → "
                        f"ConvexHull closed ring with {len(closed)} vertices"],
        "source": "kml",
    }


def _area_ha(hull) -> float:
    """Project the hull to UTM zone 43N (EPSG:32643) and return area in hectares."""
    try:
        from pyproj import Transformer
        from shapely.ops import transform

        transformer = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
        projected = transform(transformer.transform, hull)
        return projected.area / 10_000
    except Exception as exc:
        logger.warning("Area computation failed (%s) — returning 0", exc)
        return 0.0

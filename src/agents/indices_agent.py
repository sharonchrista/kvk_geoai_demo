import os
import logging
import hashlib
import httpx
import base64
from datetime import date, timedelta

logger = logging.getLogger(__name__)

CDSE_CLIENT_ID = os.getenv("CDSE_CLIENT_ID")
CDSE_CLIENT_SECRET = os.getenv("CDSE_CLIENT_SECRET")

def _date_range():
    end = date.today()
    start = end - timedelta(days=60)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

def _get_cdse_token() -> str:
    if not CDSE_CLIENT_ID or not CDSE_CLIENT_SECRET:
        return ""
    url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    data = {"grant_type": "client_credentials", "client_id": CDSE_CLIENT_ID, "client_secret": CDSE_CLIENT_SECRET}
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(url, data=data)
            res.raise_for_status()
            return res.json().get("access_token", "")
    except Exception as e:
        logger.error(f"CDSE Auth failed: {e}")
        return ""

def _get_bounding_box(geojson_polygon):
    coords = geojson_polygon["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [min(lons), min(lats), max(lons), max(lats)]

def _generate_dynamic_evalscript(index_name: str, min_val: float, max_val: float) -> str:
    """
    Dynamically generates an Evalscript that stretches the 11-class color ramp
    across the field's SPECIFIC minimum and maximum values.
    """
    # Guard against perfectly uniform fields (prevents division by zero)
    if (max_val - min_val) < 0.05:
        min_val -= 0.05
        max_val += 0.05

    # Calculate exactly 12 stops (for 11 intervals) between the field's actual min and max
    step = (max_val - min_val) / 11.0
    stops = [round(min_val + (i * step), 4) for i in range(12)]
    stops_str = ", ".join(map(str, stops))

    # Select the correct mathematical formula
    formula = "let val = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);"
    if index_name == "EVI":
        formula = "let val = 2.5 * ((sample.B08 - sample.B04) / (sample.B08 + 6.0 * sample.B04 - 7.5 * sample.B02 + 1.0));"
    elif index_name == "NDWI":
        formula = "let val = (sample.B03 - sample.B08) / (sample.B03 + sample.B08);"
    elif index_name == "SAVI":
        formula = "let L = 0.428; let val = ((sample.B08 - sample.B04) / (sample.B08 + sample.B04 + L)) * (1.0 + L);"

    # Switch color palettes based on the index type
    if index_name == "NDWI":
        # Moisture Palette: Red (Dry) -> Yellow -> Blues (Wet)
        color_array = """[
          [0.7, 0.0, 0.0, 1], // Severely Dry Condition (Red)
          [0.9, 0.4, 0.0, 1], // Dry / Moisture Stress (Orange)
          [1.0, 0.8, 0.0, 1], // Very Low Moisture (Yellow)
          [0.8, 0.9, 0.3, 1], // Low Moisture (Pale Green)
          [0.5, 0.9, 0.7, 1], // Slightly Moderate (Cyan)
          [0.3, 0.7, 0.9, 1], // Moderate Moisture (Light Blue)
          [0.1, 0.5, 0.9, 1], // Good Moisture (Blue)
          [0.0, 0.3, 0.8, 1], // High Moisture (Dark Blue)
          [0.0, 0.1, 0.6, 1], // Very High Moisture (Navy)
          [0.0, 0.0, 0.4, 1], // Saturated / Waterlogged
          [0.0, 0.0, 0.2, 1], // Open Water
          [0.0, 0.0, 0.2, 1]  // Cap
        ]"""
    elif index_name == "EVI":
        # High Biomass Palette: Mint -> Bright Green -> Deep Teal
        # (EVI focuses on dense canopies, teal helps distinguish high density)
        color_array = """[
          [1.0, 0.9, 0.8, 1], // Bare Soil (Pale Sand)
          [0.8, 1.0, 0.8, 1], // Very Low (Pale Mint)
          [0.5, 1.0, 0.7, 1], // Low (Mint)
          [0.3, 0.9, 0.5, 1], // Moderately Low (Light Green)
          [0.1, 0.8, 0.4, 1], // Moderate (Green)
          [0.0, 0.7, 0.4, 1], // Moderately High (Emerald)
          [0.0, 0.6, 0.5, 1], // High (Teal)
          [0.0, 0.5, 0.5, 1], // Very High (Dark Teal)
          [0.0, 0.4, 0.6, 1], // Dense (Blue-Green)
          [0.0, 0.3, 0.5, 1], // Extremely Dense (Dark Blue-Green)
          [0.0, 0.2, 0.4, 1], // Saturation Cap
          [0.0, 0.2, 0.4, 1]
        ]"""
    elif index_name == "SAVI":
        # Soil Adjusted Palette (Matches Frontend Legend): White -> Pink -> Dark Purple
        color_array = """[
          [1.0, 1.0, 1.0, 1],    // 0.0 (Bare Soil - White)
          [0.95, 0.8, 0.9, 1],   // Light Pink
          [0.9, 0.6, 0.8, 1],    // Pink
          [0.85, 0.4, 0.7, 1],   // Darker Pink
          [0.8, 0.2, 0.6, 1],    // Magenta-Pink
          [0.75, 0.1, 0.5, 1],   // Magenta (~0.5)
          [0.6, 0.0, 0.4, 1],    // Dark Magenta
          [0.5, 0.0, 0.35, 1],   // Purple
          [0.4, 0.0, 0.3, 1],    // Dark Purple
          [0.3, 0.0, 0.25, 1],   // Very Dark Purple
          [0.2, 0.0, 0.2, 1],    // 1.0 (High Veg - Deepest Purple)
          [0.2, 0.0, 0.2, 1]     // Cap
        ]"""
    else:
        # NDVI (Default): Classic Vegetation Palette: Red -> Yellow -> Green
        color_array = """[
          [0.8, 0.0, 0.0, 1], // Severe Stress/Water (Dark Red)
          [1.0, 0.2, 0.0, 1], // High Stress (Red)
          [1.0, 0.5, 0.0, 1], // Moderate Stress (Orange)
          [1.0, 0.8, 0.0, 1], // Low Veg (Yellow-Orange)
          [1.0, 1.0, 0.0, 1], // Early Growth (Yellow)
          [0.8, 1.0, 0.0, 1], // Moderate Veg (Yellow-Green)
          [0.6, 0.9, 0.0, 1], // Good Veg (Light Green)
          [0.4, 0.8, 0.0, 1], // Healthy (Medium Green)
          [0.2, 0.6, 0.0, 1], // Very Healthy (Green)
          [0.0, 0.5, 0.0, 1], // Dense Canopy (Dark Green)
          [0.0, 0.3, 0.0, 1], // Extremely Dense (Very Dark Green)
          [0.0, 0.3, 0.0, 1]
        ]"""

    return f"""
    //VERSION=3
    function setup() {{ return {{ input: ["B02", "B03", "B04", "B08", "dataMask"], output: {{ bands: 4 }} }}; }}
    function evaluatePixel(sample) {{
      if (sample.dataMask === 0) return [0, 0, 0, 0];
      
      {formula}
      
      return colorBlend(val, [{stops_str}], {color_array});
    }}
    """

def _cdse_indices_or_mock(geojson_polygon: dict) -> dict:
    try:
        return _cdse_indices(geojson_polygon)
    except Exception as exc:
        logger.warning(f"CDSE fallback to mock: {exc}")
        return _mock_indices(geojson_polygon)

def _cdse_indices(geojson_polygon: dict) -> dict:
    token = _get_cdse_token()
    if not token: raise ValueError("Missing Token")

    start, end = _date_range()
    
    # 1. FETCH STATS FIRST
    stats = _get_real_statistics(geojson_polygon, token, start, end)
    logger.info("CDSE Statistics API successfully extracted dynamic ranges.")

    capture_date = stats.get("capture_date")
    if capture_date:
        time_from = f"{capture_date}T00:00:00Z"
        time_to = f"{capture_date}T23:59:59Z"
    else:
        time_from = f"{start}T00:00:00Z"
        time_to = f"{end}T23:59:59Z"

    # 2. GENERATE IMAGES WITH DYNAMIC STRETCHING AND POLYGON CLIPPING
    base64_images = {}
    url = "https://sh.dataspace.copernicus.eu/api/v1/process"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "image/png"
    }
    
    layers_to_process = ["NDVI", "EVI", "NDWI", "SAVI"]
    
    with httpx.Client(timeout=20.0) as client:
        for layer in layers_to_process:
            layer_min = stats.get(f"{layer}_min", 0.0)
            layer_max = stats.get(f"{layer}_max", 1.0)
            custom_evalscript = _generate_dynamic_evalscript(layer, layer_min, layer_max)

            payload = {
                "input": {
                    "bounds": {
                        "geometry": geojson_polygon, 
                        "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                    },
                    "data": [
                        {
                            "type": "sentinel-2-l2a", 
                            "dataFilter": {
                                "timeRange": {"from": time_from, "to": time_to}, 
                                "maxCloudCoverage": 20
                            },
                            "processing": {
                                "upsampling": "BILINEAR",
                                "downsampling": "BILINEAR"
                            }
                        }
                    ]
                },
                "output": {
                    "width": 1024, 
                    "height": 1024, 
                    "responses": [{"identifier": "default", "format": {"type": "image/png"}}]
                },
                "evalscript": custom_evalscript
            }
            
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            base64_images[layer] = f"data:image/png;base64,{base64.b64encode(response.content).decode('utf-8')}"

    return {
        "NDVI": stats["NDVI"], "EVI": stats["EVI"], "NDWI": stats["NDWI"], "SAVI": stats["SAVI"],
        "capture_date": capture_date,
        "base64_images": base64_images,
        "source": "cdse_processing_api",
        "search_window": f"{start} -> {end}"
    }

def _get_real_statistics(geojson_polygon: dict, token: str, start: str, end: str) -> dict:
    url = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    stats_evalscript = """
    //VERSION=3
    function setup() {
        return {
            input: ["B02", "B03", "B04", "B08", "dataMask"],
            output: [
                { id: "ndvi", bands: 1 },
                { id: "evi", bands: 1 },
                { id: "ndwi", bands: 1 },
                { id: "savi", bands: 1 },
                { id: "dataMask", bands: 1 }
            ]
        };
    }
    function evaluatePixel(sample) {
        let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);
        let evi = 2.5 * ((sample.B08 - sample.B04) / (sample.B08 + 6.0 * sample.B04 - 7.5 * sample.B02 + 1.0));
        let ndwi = (sample.B03 - sample.B08) / (sample.B03 + sample.B08);
        
        let L = 0.428;
        let savi = ((sample.B08 - sample.B04) / (sample.B08 + sample.B04 + L)) * (1.0 + L);
        
        return { 
            ndvi: [ndvi], 
            evi: [evi], 
            ndwi: [ndwi], 
            savi: [savi], 
            dataMask: [sample.dataMask] 
        };
    }
    """

    payload = {
        "input": {
            "bounds": {
                "geometry": geojson_polygon,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": 20}}]
        },
        "aggregation": {
            "timeRange": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": stats_evalscript,
            "resx": 0.0001,
            "resy": 0.0001
        }
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(url, headers=headers, json=payload)
            if res.status_code != 200:
                logger.error(f"CDSE API Error Details: {res.text}")
            res.raise_for_status()
            data = res.json()

            for interval in reversed(data.get("data", [])):
                outputs = interval.get("outputs")
                if outputs and outputs["ndvi"]["bands"]["B0"]["stats"]["sampleCount"] > 0:
                    
                    # Extract the date from the interval payload
                    raw_time = interval.get("interval", {}).get("from", "")
                    capture_date = raw_time.split("T")[0] if "T" in raw_time else raw_time

                    return {
                        "capture_date": capture_date, 
                        "NDVI": round(outputs["ndvi"]["bands"]["B0"]["stats"]["mean"], 3),
                        "NDVI_min": outputs["ndvi"]["bands"]["B0"]["stats"]["min"],
                        "NDVI_max": outputs["ndvi"]["bands"]["B0"]["stats"]["max"],
                        "EVI": round(outputs["evi"]["bands"]["B0"]["stats"]["mean"], 3),
                        "EVI_min": outputs["evi"]["bands"]["B0"]["stats"]["min"],
                        "EVI_max": outputs["evi"]["bands"]["B0"]["stats"]["max"],
                        "NDWI": round(outputs["ndwi"]["bands"]["B0"]["stats"]["mean"], 3),
                        "NDWI_min": outputs["ndwi"]["bands"]["B0"]["stats"]["min"],
                        "NDWI_max": outputs["ndwi"]["bands"]["B0"]["stats"]["max"],
                        "SAVI": round(outputs["savi"]["bands"]["B0"]["stats"]["mean"], 3),
                        "SAVI_min": outputs["savi"]["bands"]["B0"]["stats"]["min"],
                        "SAVI_max": outputs["savi"]["bands"]["B0"]["stats"]["max"]
                    }
                    
            return {"capture_date": None, "NDVI": 0.0, "EVI": 0.0, "NDWI": 0.0, "SAVI": 0.0}
            
    except Exception as e:
        logger.error(f"CDSE Statistics API failed: {e}")
        return {"capture_date": None, "NDVI": 0.0, "EVI": 0.0, "NDWI": 0.0, "SAVI": 0.0}

def _mock_indices(geojson_polygon: dict) -> dict:
    coords = geojson_polygon["coordinates"][0]
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    h = int(hashlib.md5(f"{cy:.5f},{cx:.5f}".encode()).hexdigest(), 16)
    def pseudo(shift, lo, hi): return round(lo + ((h >> shift) & 0xFFFF) / 0xFFFF * (hi - lo), 3)
    ndvi = pseudo(0, 0.38, 0.87)
    return {
        "capture_date": date.today().strftime("%Y-%m-%d"), 
        "NDVI": round(ndvi, 3), "EVI": round(pseudo(4, ndvi * 0.75, ndvi * 0.95), 3),
        "NDWI": round(pseudo(8, -0.05, 0.38), 3), "SAVI": round(pseudo(12, ndvi * 0.65, ndvi * 0.85), 3),
        "base64_images": {}, "source": "mock", "search_window": "mock"
    }
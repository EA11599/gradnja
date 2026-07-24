@'
#!/usr/bin/env python3
"""
Automatski dohvaća broj zgrada u Gradu Zagrebu kroz vrijeme s ohsome API-ja
(OSM History Analytics, Sveučilište u Heidelbergu) i sprema rezultat kao JSON.

Ova skripta se pokreće automatski putem GitHub Actionsa (vidi
.github/workflows/update-data.yml) — nema ručnih koraka.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OHSOME_URL = "https://api.ohsome.org/v1/elements/count"

FALLBACK_GEOJSON_PATH = Path(__file__).parent / "zagreb_boundary_fallback.geojson"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "buildings_timeseries.json"

START_DATE = "2012-01-01"
INTERVAL = "P3M"


def get_zagreb_boundary() -> dict:
    if FALLBACK_GEOJSON_PATH.exists():
        with open(FALLBACK_GEOJSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    params = {
        "q": "Grad Zagreb, Hrvatska",
        "format": "json",
        "polygon_geojson": 1,
        "limit": 1,
    }
    headers = {
        "User-Agent": "zagreb-gradnja-izvjestaj/1.0 (github actions bot; automated report)",
        "Accept": "application/json",
    }
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise RuntimeError(
            "Nominatim nije pronašao granicu Grada Zagreba. "
            "Ubaci ručno preuzet GeoJSON u scripts/zagreb_boundary_fallback.geojson"
        )
    return results[0]["geojson"]


def fetch_building_timeseries(boundary_geojson: dict) -> list:
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "zagreb"},
                "geometry": boundary_geojson,
            }
        ],
    }
    payload = {
        "bpolys": json.dumps(feature_collection),
        "filter": "building=* and (type:way or type:relation)",
        "time": f"{START_DATE}/{end_date}/{INTERVAL}",
        "format": "json",
    }
    headers = {
        "User-Agent": "zagreb-gradnja-izvjestaj/1.0 (github actions bot; automated report)",
        "Accept": "application/json",
    }
    resp = requests.post(OHSOME_URL, data=payload, headers=headers, timeout=180)
    resp.raise_for_status()
    body = resp.json()
    return body.get("result", [])


def main() -> int:
    try:
        boundary = get_zagreb_boundary()
        series = fetch_building_timeseries(boundary)
    except Exception as exc:
        print(f"GREŠKA prilikom dohvaćanja podataka: {exc}", file=sys.stderr)
        return 1

    if not series:
        print("Upozorenje: ohsome API vratio je prazan rezultat.", file=sys.stderr)
        return 1

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "source": "ohsome API (api.ohsome.org)",
        "area": "Grad Zagreb",
        "series": series,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Spremljeno {len(series)} točaka u {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'@ | Out-File -Encoding utf8 "scripts\fetch_data.py"
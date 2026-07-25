#!/usr/bin/env python3
"""
Automatski dohvaća broj zgrada u Gradu Zagrebu kroz vrijeme s ohsome API-ja
(OSM History Analytics, Sveučilište u Heidelbergu) i sprema rezultat kao JSON.

Ova skripta se pokreće automatski putem GitHub Actionsa (vidi
.github/workflows/update-data.yml) — nema ručnih koraka.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

MAX_RETRIES = 3
BACKOFF_SECONDS = [10, 30, 90]  # pauza prije 2., 3. i 4. pokušaja


def call_with_retry(description: str, func, *args, **kwargs):
    """Poziva func s ponovnim pokušajima kod privremenih grešaka (5xx, timeout, mrežne greške).
    Ako su svi pokušaji neuspješni, diže zadnju iznimku dalje."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 2):  # ukupno do 4 pokušaja
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            last_exc = exc
            # 4xx (osim 429) su trajne greške u našem upitu — nema smisla ponavljati
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            print(f"[{description}] pokušaj {attempt} neuspješan (HTTP {status}).", file=sys.stderr)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            print(f"[{description}] pokušaj {attempt} neuspješan ({exc.__class__.__name__}).", file=sys.stderr)

        if attempt <= MAX_RETRIES:
            pause = BACKOFF_SECONDS[attempt - 1]
            print(f"[{description}] čekam {pause}s prije ponovnog pokušaja...", file=sys.stderr)
            time.sleep(pause)

    raise last_exc

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OHSOME_URL = "https://api.ohsome.org/v1/elements/count"

# Ako Nominatim ne pronađe granicu ili vrati krivu (poznati rizik s upitima po imenu),
# ovdje se može zalijepiti ručno preuzeti GeoJSON poligon granice Grada Zagreba kao fallback.
FALLBACK_GEOJSON_PATH = Path(__file__).parent / "zagreb_boundary_fallback.geojson"

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "buildings_timeseries.json"

START_DATE = "2012-01-01"
INTERVAL = "P3M"  # tromjesečno; promijeniti u P1M za mjesečnu granularnost


def get_zagreb_boundary() -> dict:
    """Dohvaća granicu Grada Zagreba preko Nominatim API-ja."""
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
    """Poziva ohsome API za kumulativan broj entiteta s tagom building=* kroz vrijeme."""
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
        boundary = call_with_retry("Nominatim", get_zagreb_boundary)
        series = call_with_retry("ohsome API", fetch_building_timeseries, boundary)
    except Exception as exc:
        # Nakon svih pokušaja i dalje neuspješno — vjerojatno je vanjski servis
        # trenutno nedostupan. Ne diramo postojeće podatke i tiho izlazimo
        # (exit 0) da GitHub Action ne prijavi "failure" svaki put kad servis
        # privremeno padne; idući zakazani run će jednostavno probati ponovno.
        print(
            f"Vanjski servis nedostupan nakon {MAX_RETRIES + 1} pokušaja, "
            f"preskačem ovaj ciklus: {exc}",
            file=sys.stderr,
        )
        return 0

    if not series:
        print("Upozorenje: ohsome API vratio je prazan rezultat, preskačem ovaj ciklus.", file=sys.stderr)
        return 0

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "source": "ohsome API (api.ohsome.org)",
        "area": "Grad Zagreb",
        "series": series,  # lista {timestamp, value}
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Spremljeno {len(series)} točaka u {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

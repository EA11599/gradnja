#!/usr/bin/env python3
"""
Prati NOVE zgrade u Gradu Zagrebu preko Overpass API-ja, klasificirane po tipu
(stambene zgrade, kuće, garaže, poslovni objekti, nepoznato/generičko).

Pristup:
1. Svako pokretanje dohvati ID + building tag SVIH trenutnih zgrada (way +
   relation s tagom building=*) unutar bounding boxa Grada Zagreba.
2. Klasificira svaku zgradu po vrijednosti building taga u jednu od kategorija.
3. Usporedi s popisom iz prošlog pokretanja — nove zgrade = ID-jevi koji su
   sad prisutni, a prije nisu bili.
4. Doda točku u vremensku seriju (ukupno + po kategoriji, uključujući koliko
   je novo od zadnjeg pokretanja), i sprema trenutno stanje za sljedeću
   usporedbu.

Nema ručnih koraka — pokreće se automatski putem GitHub Actionsa
(.github/workflows/update-data.yml).
"""

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Bounding box Grada Zagreba (min_lat, min_lon, max_lat, max_lon).
ZAGREB_BBOX = (45.75, 15.8, 45.9, 16.15)

TIMESERIES_PATH = Path(__file__).parent.parent / "data" / "buildings_timeseries.json"
SNAPSHOT_PATH = Path(__file__).parent.parent / "data" / "buildings_snapshot_ids.json"

MAX_RETRIES = 2
BACKOFF_SECONDS = [15, 45]

# Mapiranje OSM building=* vrijednosti u naše kategorije.
CATEGORY_MAP = {
    "apartments": "stambene",
    "dormitory": "stambene",
    "house": "kuce",
    "detached": "kuce",
    "semidetached_house": "kuce",
    "terrace": "kuce",
    "bungalow": "kuce",
    "cabin": "kuce",
    "garage": "garaze",
    "garages": "garaze",
    "carport": "garaze",
    "commercial": "poslovno",
    "retail": "poslovno",
    "office": "poslovno",
    "industrial": "poslovno",
    "warehouse": "poslovno",
    "supermarket": "poslovno",
    "kiosk": "poslovno",
    "hotel": "poslovno",
}
CATEGORY_LABELS = {
    "stambene": "Stambene zgrade",
    "kuce": "Kuće",
    "garaze": "Garaže",
    "poslovno": "Poslovni objekti",
    "ostalo": "Ostalo (poznat tip)",
    "nepoznato": "Nepoznato / generičko (building=yes)",
}


def classify(building_tag_value: str) -> str:
    if building_tag_value == "yes":
        return "nepoznato"
    return CATEGORY_MAP.get(building_tag_value, "ostalo")


def build_query() -> str:
    lat1, lon1, lat2, lon2 = ZAGREB_BBOX
    return (
        f'[out:json][timeout:150];'
        f'(way["building"]({lat1},{lon1},{lat2},{lon2});'
        f'relation["building"]({lat1},{lon1},{lat2},{lon2}););'
        f'out tags;'
    )


def fetch_buildings() -> dict:
    """Vraća dict {id: kategorija} za sve zgrade u bboxu."""
    query = build_query()
    headers = {
        "User-Agent": "zagreb-gradnja-izvjestaj/1.0 (github actions bot; automated report)",
    }
    last_exc = None

    for mirror in OVERPASS_MIRRORS:
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = requests.post(
                    mirror, data={"data": query}, headers=headers, timeout=180
                )
                resp.raise_for_status()
                body = resp.json()
                if "remark" in body:
                    raise RuntimeError(f"Overpass remark: {body['remark']}")
                result = {}
                for el in body.get("elements", []):
                    if el.get("type") not in ("way", "relation"):
                        continue
                    element_id = f"{el['type']}/{el['id']}"
                    building_value = el.get("tags", {}).get("building", "yes")
                    result[element_id] = classify(building_value)
                if not result:
                    raise RuntimeError("Overpass je vratio prazan skup elemenata.")
                return result
            except Exception as exc:
                last_exc = exc
                print(f"[{mirror}] pokušaj {attempt} neuspješan: {exc}", file=sys.stderr)
                if attempt <= MAX_RETRIES:
                    pause = BACKOFF_SECONDS[attempt - 1]
                    print(f"[{mirror}] čekam {pause}s prije ponovnog pokušaja...", file=sys.stderr)
                    time.sleep(pause)

    raise last_exc if last_exc else RuntimeError("Nepoznata greška prilikom dohvaćanja podataka.")


def load_previous() -> dict:
    if not SNAPSHOT_PATH.exists():
        return {}
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("buildings", {})
    except Exception:
        return {}


def load_timeseries() -> list:
    if not TIMESERIES_PATH.exists():
        return []
    try:
        with open(TIMESERIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("series", [])
    except Exception:
        return []


def main() -> int:
    try:
        current = fetch_buildings()  # {id: category}
    except Exception as exc:
        print(f"Overpass nedostupan nakon svih pokušaja, preskačem ovaj ciklus: {exc}", file=sys.stderr)
        return 0

    previous = load_previous()
    is_first_run = len(previous) == 0

    previous_ids = set(previous.keys())
    current_ids = set(current.keys())
    new_ids = current_ids - previous_ids if not is_first_run else set()

    totals_by_category = Counter(current.values())
    new_by_category = Counter(current[i] for i in new_ids)

    now = datetime.now(timezone.utc).isoformat()

    series = load_timeseries()
    series.append(
        {
            "timestamp": now,
            "total": len(current),
            "new_since_last": len(new_ids) if not is_first_run else 0,
            "by_category": dict(totals_by_category),
            "new_by_category": dict(new_by_category) if not is_first_run else {},
        }
    )

    TIMESERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TIMESERIES_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_updated": now,
                "source": "Overpass API (overpass-api.de i mirroru)",
                "area_bbox": list(ZAGREB_BBOX),
                "note": "Pratimo promjene od početka praćenja, ne punu povijest OSM-a.",
                "category_labels": CATEGORY_LABELS,
                "series": series,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": now, "buildings": current}, f)

    if is_first_run:
        print(f"Prvo pokretanje: zabilježeno {len(current)} zgrada. Raspodjela: {dict(totals_by_category)}")
    else:
        print(f"Ukupno {len(current)} zgrada, {len(new_ids)} novih. Nove po kategoriji: {dict(new_by_category)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

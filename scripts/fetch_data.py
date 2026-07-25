#!/usr/bin/env python3
"""
Prati NOVE zgrade u Gradu Zagrebu preko Overpass API-ja, klasificirane po tipu,
i vodi dnevnik SVAKE pojedinačne nove zgrade (lokacija + svi poznati podaci).

Pristup:
1. Svako pokretanje dohvati ID, tagove i centralnu točku (lat/lon) SVIH
   trenutnih zgrada (way + relation s tagom building=*) unutar bounding
   boxa Grada Zagreba.
2. Klasificira svaku zgradu po vrijednosti building taga.
3. Usporedi s popisom ID-jeva iz prošlog pokretanja — nove zgrade = ID-jevi
   koji su sad prisutni, a prije nisu bili.
4. Za svaku novu zgradu doda zapis u dnevnik (data/new_buildings_log.json)
   s lokacijom i svim tagovima — za prikaz na dashboardu.
5. Doda točku u agregiranu vremensku seriju (ukupno + po kategoriji).

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

ZAGREB_BBOX = (45.75, 15.8, 45.9, 16.15)

TIMESERIES_PATH = Path(__file__).parent.parent / "data" / "buildings_timeseries.json"
SNAPSHOT_PATH = Path(__file__).parent.parent / "data" / "buildings_snapshot_ids.json"
NEW_LOG_PATH = Path(__file__).parent.parent / "data" / "new_buildings_log.json"

MAX_RETRIES = 2
BACKOFF_SECONDS = [15, 45]

CATEGORY_MAP = {
    "apartments": "stambene", "dormitory": "stambene",
    "house": "kuce", "detached": "kuce", "semidetached_house": "kuce",
    "terrace": "kuce", "bungalow": "kuce", "cabin": "kuce",
    "garage": "garaze", "garages": "garaze", "carport": "garaze",
    "commercial": "poslovno", "retail": "poslovno", "office": "poslovno",
    "industrial": "poslovno", "warehouse": "poslovno",
    "supermarket": "poslovno", "kiosk": "poslovno", "hotel": "poslovno",
}
CATEGORY_LABELS = {
    "stambene": "Stambene zgrade",
    "kuce": "Kuće",
    "garaze": "Garaže",
    "poslovno": "Poslovni objekti",
    "ostalo": "Ostalo (poznat tip)",
    "stambene_procjena": "Vjerojatno stambene (procjena po broju etaža)",
    "kuce_procjena": "Vjerojatno kuće (procjena po broju etaža)",
    "nepoznato": "Nepoznato (bez dovoljno podataka za procjenu)",
}

MAX_LOG_ENTRIES = 2000  # sigurnosna granica da dnevnik ne raste unedogled


def parse_levels(value: str):
    if not value:
        return None
    first = value.split(";")[0].strip()
    try:
        return float(first)
    except ValueError:
        return None


def classify(tags: dict) -> str:
    building_value = tags.get("building", "yes")
    if building_value != "yes":
        return CATEGORY_MAP.get(building_value, "ostalo")
    levels = parse_levels(tags.get("building:levels", ""))
    if levels is None:
        return "nepoznato"
    return "kuce_procjena" if levels <= 2 else "stambene_procjena"


def build_query() -> str:
    lat1, lon1, lat2, lon2 = ZAGREB_BBOX
    return (
        f'[out:json][timeout:180];'
        f'(way["building"]({lat1},{lon1},{lat2},{lon2});'
        f'relation["building"]({lat1},{lon1},{lat2},{lon2}););'
        f'out tags center;'
    )


def fetch_buildings() -> dict:
    """Vraća dict {id: {category, tags, lat, lon, osm_type}} za sve zgrade u bboxu."""
    query = build_query()
    headers = {"User-Agent": "zagreb-gradnja-izvjestaj/1.0 (github actions bot; automated report)"}
    last_exc = None

    for mirror in OVERPASS_MIRRORS:
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = requests.post(mirror, data={"data": query}, headers=headers, timeout=200)
                resp.raise_for_status()
                body = resp.json()
                if "remark" in body:
                    raise RuntimeError(f"Overpass remark: {body['remark']}")
                result = {}
                for el in body.get("elements", []):
                    if el.get("type") not in ("way", "relation"):
                        continue
                    element_id = f"{el['type']}/{el['id']}"
                    tags = el.get("tags", {})
                    center = el.get("center", {})
                    result[element_id] = {
                        "category": classify(tags),
                        "tags": tags,
                        "lat": center.get("lat"),
                        "lon": center.get("lon"),
                        "osm_type": el["type"],
                        "osm_numeric_id": el["id"],
                    }
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


def load_previous_ids() -> set:
    if not SNAPSHOT_PATH.exists():
        return set()
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # podržava i stari format (lista) i noviji ({"buildings": {...}})
        if "ids" in data:
            return set(data["ids"])
        if "buildings" in data:
            return set(data["buildings"].keys())
        return set()
    except Exception:
        return set()


def load_timeseries() -> list:
    if not TIMESERIES_PATH.exists():
        return []
    try:
        with open(TIMESERIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("series", [])
    except Exception:
        return []


def load_new_log() -> list:
    if not NEW_LOG_PATH.exists():
        return []
    try:
        with open(NEW_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("entries", [])
    except Exception:
        return []


def osm_url(osm_type: str, numeric_id: int, lat, lon) -> str:
    # Link na kartu centriranu na lokaciju (s markerom) — ovo uvijek radi.
    # Ako postoje koordinate, koristi njih; inače kao rezervu link na sam element.
    if lat is not None and lon is not None:
        return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=19/{lat}/{lon}"
    return f"https://www.openstreetmap.org/{osm_type}/{numeric_id}"


def main() -> int:
    try:
        current = fetch_buildings()  # {id: {category, tags, lat, lon, osm_type, osm_numeric_id}}
    except Exception as exc:
        print(f"Overpass nedostupan nakon svih pokušaja, preskačem ovaj ciklus: {exc}", file=sys.stderr)
        return 0

    previous_ids = load_previous_ids()
    is_first_run = len(previous_ids) == 0
    current_ids = set(current.keys())
    new_ids = current_ids - previous_ids if not is_first_run else set()

    totals_by_category = Counter(v["category"] for v in current.values())
    new_by_category = Counter(current[i]["category"] for i in new_ids)

    now = datetime.now(timezone.utc).isoformat()

    # --- Agregirana vremenska serija ---
    series = load_timeseries()
    series.append({
        "timestamp": now,
        "total": len(current),
        "new_since_last": len(new_ids) if not is_first_run else 0,
        "by_category": dict(totals_by_category),
        "new_by_category": dict(new_by_category) if not is_first_run else {},
    })
    TIMESERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TIMESERIES_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": now,
            "source": "Overpass API (overpass-api.de i mirroru)",
            "area_bbox": list(ZAGREB_BBOX),
            "note": "Pratimo promjene od početka praćenja, ne punu povijest OSM-a.",
            "category_labels": CATEGORY_LABELS,
            "series": series,
        }, f, ensure_ascii=False, indent=2)

    # --- Dnevnik pojedinačnih novih zgrada ---
    if not is_first_run and new_ids:
        log_entries = load_new_log()
        for element_id in sorted(new_ids):
            b = current[element_id]
            log_entries.append({
                "detected_at": now,
                "id": element_id,
                "osm_type": b["osm_type"],
                "osm_numeric_id": b["osm_numeric_id"],
                "osm_url": osm_url(b["osm_type"], b["osm_numeric_id"], b["lat"], b["lon"]),
                "category": b["category"],
                "lat": b["lat"],
                "lon": b["lon"],
                "tags": b["tags"],
            })
        log_entries = log_entries[-MAX_LOG_ENTRIES:]
        with open(NEW_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "last_updated": now,
                "category_labels": CATEGORY_LABELS,
                "entries": log_entries,
            }, f, ensure_ascii=False, indent=2)

    # --- Snapshot ID-jeva za sljedeću usporedbu (lagan, samo ID-jevi) ---
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": now, "ids": sorted(current_ids)}, f)

    if is_first_run:
        print(f"Prvo pokretanje: zabilježeno {len(current)} zgrada. Raspodjela: {dict(totals_by_category)}")
    else:
        print(f"Ukupno {len(current)} zgrada, {len(new_ids)} novih. Nove po kategoriji: {dict(new_by_category)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

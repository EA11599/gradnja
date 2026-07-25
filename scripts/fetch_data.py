#!/usr/bin/env python3
"""
Prati NOVE zgrade u Gradu Zagrebu preko Overpass API-ja.

Pristup (namjerno jednostavan i pouzdan, umjesto povijesne rekonstrukcije
koja se pokazala preskupom za javne Overpass servere):

1. Svako pokretanje dohvati ID-jeve SVIH trenutnih zgrada (way + relation
   s tagom building=*) unutar bounding boxa Grada Zagreba.
2. Usporedi taj popis s popisom iz prošlog pokretanja (spremljenim lokalno).
3. Nove zgrade = ID-jevi koji su sad prisutni, a prije nisu bili.
4. Doda jednu točku u kumulativnu vremensku seriju (ukupno + koliko je
   novo od zadnjeg pokretanja), i sprema trenutni popis ID-jeva za
   sljedeću usporedbu.

Nema ručnih koraka — pokreće se automatski putem GitHub Actionsa
(.github/workflows/update-data.yml).
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Nekoliko javnih Overpass instanci — ako je jedna zauzeta, probamo sljedeću.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Bounding box Grada Zagreba (min_lat, min_lon, max_lat, max_lon).
# Namjerno jednostavan i brz pristup koji je potvrđeno pouzdan (za razliku
# od area["name"=...] pretrage, koja izaziva timeout na javnim serverima).
ZAGREB_BBOX = (45.75, 15.8, 45.9, 16.15)

TIMESERIES_PATH = Path(__file__).parent.parent / "data" / "buildings_timeseries.json"
SNAPSHOT_IDS_PATH = Path(__file__).parent.parent / "data" / "buildings_snapshot_ids.json"

MAX_RETRIES = 2  # po mirroru
BACKOFF_SECONDS = [15, 45]


def build_query() -> str:
    lat1, lon1, lat2, lon2 = ZAGREB_BBOX
    return (
        f'[out:json][timeout:120];'
        f'(way["building"]({lat1},{lon1},{lat2},{lon2});'
        f'relation["building"]({lat1},{lon1},{lat2},{lon2}););'
        f'out ids;'
    )


def fetch_building_ids() -> set:
    """Dohvaća skup ID-jeva svih zgrada u bboxu, probajući više mirrora s retry logikom."""
    query = build_query()
    headers = {
        "User-Agent": "zagreb-gradnja-izvjestaj/1.0 (github actions bot; automated report)",
    }
    last_exc = None

    for mirror in OVERPASS_MIRRORS:
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = requests.post(
                    mirror, data={"data": query}, headers=headers, timeout=150
                )
                resp.raise_for_status()
                body = resp.json()
                if "remark" in body:
                    # Overpass ponekad vrati HTTP 200 s ugniježđenom greškom (npr. timeout)
                    raise RuntimeError(f"Overpass remark: {body['remark']}")
                ids = {
                    f"{el['type']}/{el['id']}"
                    for el in body.get("elements", [])
                    if el.get("type") in ("way", "relation")
                }
                if not ids:
                    raise RuntimeError("Overpass je vratio prazan skup elemenata.")
                return ids
            except Exception as exc:
                last_exc = exc
                print(f"[{mirror}] pokušaj {attempt} neuspješan: {exc}", file=sys.stderr)
                if attempt <= MAX_RETRIES:
                    pause = BACKOFF_SECONDS[attempt - 1]
                    print(f"[{mirror}] čekam {pause}s prije ponovnog pokušaja...", file=sys.stderr)
                    time.sleep(pause)

    raise last_exc if last_exc else RuntimeError("Nepoznata greška prilikom dohvaćanja podataka.")


def load_previous_ids() -> set:
    if not SNAPSHOT_IDS_PATH.exists():
        return set()
    try:
        with open(SNAPSHOT_IDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("ids", []))
    except Exception:
        return set()


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
        current_ids = fetch_building_ids()
    except Exception as exc:
        # Vanjski servis nedostupan nakon svih pokušaja na svim mirrorima —
        # tiho preskačemo ovaj ciklus (exit 0), ne diramo postojeće podatke.
        # Idući zakazani run će jednostavno probati ponovno.
        print(f"Overpass nedostupan nakon svih pokušaja, preskačem ovaj ciklus: {exc}", file=sys.stderr)
        return 0

    previous_ids = load_previous_ids()
    is_first_run = len(previous_ids) == 0

    new_ids = current_ids - previous_ids if not is_first_run else set()
    now = datetime.now(timezone.utc).isoformat()

    series = load_timeseries()
    series.append(
        {
            "timestamp": now,
            "total": len(current_ids),
            "new_since_last": len(new_ids) if not is_first_run else 0,
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
                "series": series,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(SNAPSHOT_IDS_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_updated": now, "ids": sorted(current_ids)}, f)

    if is_first_run:
        print(f"Prvo pokretanje: zabilježeno {len(current_ids)} zgrada kao početna točka.")
    else:
        print(f"Ukupno {len(current_ids)} zgrada, {len(new_ids)} novih od zadnjeg pokretanja.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

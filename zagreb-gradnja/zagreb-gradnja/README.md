# Izvještaj gradnje — Grad Zagreb

Automatizirani dashboard koji prati kumulativni rast broja zgrada u Gradu
Zagrebu na temelju OpenStreetMap podataka (preko ohsome API-ja).

## Kako radi (bez ručnih koraka)

1. `.github/workflows/update-data.yml` pokreće `scripts/fetch_data.py` po
   rasporedu (default: svaki ponedjeljak, može se promijeniti u cron izrazu).
2. Skripta zove ohsome API i sprema rezultat u `data/buildings_timeseries.json`.
3. Ako se podaci promijene, GitHub Action ih sam commita natrag u repozitorij.
4. `index.html` čita taj JSON i iscrtava dashboard (kumulativni graf, graf
   novih zgrada po periodu, tablicu iteracija).

## Postavljanje (jednokratno)

1. Kreiraj novi repozitorij na GitHubu i pushaj ovaj sadržaj.
2. Settings → Pages → Source: postavi na `main` granu, root folder — ovo
   objavljuje `index.html` kao javnu web stranicu.
3. Settings → Actions → General → Workflow permissions: postavi na
   "Read and write permissions" (potrebno da Action može commitati nove podatke).
4. Actions → "Ažuriraj podatke o gradnji u Zagrebu" → Run workflow — pokreni
   ručno JEDNOM da se stvori prvi skup podataka. Nakon toga sve ide samo po
   rasporedu.

## Prilagodbe

- Učestalost dohvaćanja: promijeni `cron` u `.github/workflows/update-data.yml`.
- Granularnost vremenske serije: promijeni `INTERVAL` u `scripts/fetch_data.py`
  (`P1M` = mjesečno, `P3M` = tromjesečno, `P1Y` = godišnje).
- Ako Nominatim ne uspije pronaći granicu Zagreba (rijetko, ali moguće zbog
  rate-limitinga), ručno preuzmi GeoJSON granicu i spremi je kao
  `scripts/zagreb_boundary_fallback.geojson` — skripta će je tada koristiti
  umjesto upita.

## Sljedeći koraci (nadogradnja)

- Modul za procjenu broja stanova po zgradi (`building:flats` + formula na
  temelju tlocrtne površine i broja etaža).
- Kalibracija s DZS agregatnim podacima o stanovima u izdanim građevinskim
  dozvolama za Grad Zagreb (STS baza, stsbaza.dzs.hr).

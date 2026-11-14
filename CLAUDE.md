# CLAUDE.md – kontekst projektu Corridor Watch

## Projekt
NASA Space Apps Challenge 2026 (14–15.11.2026), wyzwanie „Dancing with the SARs”.
**Corridor Watch**: aplikacja pokazująca ruch gruntu zmierzony radarem satelitarnym NISAR (InSAR)
i wskazująca anomalie (osuwiska, osiadanie) przy infrastrukturze liniowej – gazociągach, rurociągach.
Pierwsze studium przypadku: osuwisko Portuguese Bend (Kalifornia). Planowane: Latrobe Valley,
Adelaide Hills (Australia) – czekają na dane.

## Koncepcja: case study per lokalizacja (ważne ustalenie)
- Projekt to **zbiór case study** – każda lokalizacja jest osobnym studium przypadku z **innym zestawem
  obiektów i zagrożeń**: np. w jednej gazociągi i lasy, w innej budynki blisko wody i tory kolejowe itd.
- W UI każda lokalizacja = **osobna zakładka** (12 lokalizacji → 12 case study → 12 zakładek).
  Obecnie wybór lokalizacji to lista rozwijana (`<select>`) w nagłówku – do przerobienia na zakładki.
- Wniosek dla kodu: **nie zakładaj, że każda lokalizacja ma gazociągi**. Pakiet danych i frontend są dziś
  pisane pod rurociągi (`pipes`, „X z 37 gazociągów przy anomalii”, wymagany klucz `pipes`); przy nowych
  typach obiektów (budynki, tory, lasy, woda) trzeba to uogólnić – etykiety, legendę i statystyki brać
  z danych lokalizacji, a nie wpisywać na sztywno.

## Jak współpracować z użytkownikiem
- Odpowiadaj **po polsku**; **komentarze i nazwy w kodzie po angielsku**.
- Użytkownik pracuje na **Windows, PowerShell** – polecenia podawaj w tej wersji
  (`.venv\Scripts\Activate.ps1`, `Remove-Item`, `curl.exe`, `tree /f`).
- Zna Pythona najlepiej. Ma się liczyć działający efekt; kod może być generowany, ale krótko wyjaśniaj, co zmieniasz.
- Dużo pracy będzie przy **przyjaznym interfejsie użytkownika** (frontend).
- **Nie rób commitów ani pushy bez pytania.** Konwencja wiadomości: `feat:`, `fix:`, `chore:`, `docs:`, `test:` (po angielsku).
- Nie twierdź, że coś przetestowałeś, jeśli tego nie uruchomiłeś.

## Podział pracy (ważne ustalenie)
- **Analizę danych robi zespół**, osobno (z własnym Claude). Narzędziem analizy jest
  `build_location.py` z oryginalnego repo Corridor Watch (wymaga GDAL + NumPy), poza tym projektem:
  `C:\Users\DELL\OneDrive\Desktop\NASA_SPACE_APPS\corridor_watch\corridor_watch\`
  (tam też `source_data/` z GeoTIFF-ami InSAR i `locations/<id>/data.js` + `location.json`).
- **Ta aplikacja to „kontener” na gotowe wyniki**: przechowuje lokalizacje i ich pakiety danych
  oraz ładnie je wyświetla. Backend **nie liczy** analizy (wcześniejszy prototyp analizy ryzyka
  w GeoPandas został celowo usunięty).

## Struktura
```
Projekt/
  CLAUDE.md, README.md, .gitignore
  backend/                FastAPI (uruchamiany z tego folderu: uvicorn app.main:app --reload)
    app/main.py           app, CORS, GZip, routery, frontend zamontowany na "/" (na końcu)
    app/config.py         Settings (pydantic-settings, prefiks APP_, plik .env)
    app/api/locations.py  endpointy lokalizacji
    app/api/layers.py     endpointy dodatkowych warstw GeoJSON
    app/schemas/          modele Pydantic: location.py, layer.py
    app/services/         location_registry.py, location_package.py, layer_registry.py, geojson_reader.py
    scripts/import_corridor_watch.py   import location.json + data.js z folderu corridor_watch/locations
    scripts/import_team_results.py     import wyników QGIS zespołu (GeoTIFF/GPKG/CSV) → nasz pakiet + lokalizacja
    requirements-tools.txt             numpy, rasterio, pyogrio, shapely, pyproj – tylko dla skryptu importu
    data/                 dane runtime (poza Gitem): locations/locations.json, locations/<id>/data.json, layers/
  frontend/index.html     strona Corridor Watch (Leaflet, vanilla JS), dane z API
```

## Backend – najważniejsze
- Python 3.12, FastAPI, pydantic v2, pydantic-settings, python-multipart, uvicorn.
- Wzorzec: **schemas** (kształt danych) → **services** (logika, przechowywanie) → **api** (cienkie endpointy,
  mapują własne wyjątki na HTTP: 404, 409, 413, 422).
- Przechowywanie tymczasowo w plikach JSON za interfejsem rejestru (`LocationRegistry`, `LayerRegistry`):
  zapis atomowy (plik .tmp + replace), `threading.Lock`, `LocationRegistry` sam przeładowuje indeks,
  gdy zmieni go inny proces (np. skrypt importu). Docelowo można podmienić na bazę (PostgreSQL/PostGIS)
  bez zmian w endpointach. Ograniczenie: działa przy jednym procesie serwera.
- `Location`: `id` (slug `^[a-z0-9_]{3,64}$`, używany w URL `#id` i jako nazwa folderu), name, region,
  hazard, summary, order, note, view (`[[S_lat, W_lon], [N_lat, E_lon]]` – kolejność Leafleta, lat pierwsze!),
  ready, data_size_bytes, data_updated_at, created_at.
- Endpointy (`/api/v1`): `GET/POST /locations`, `GET/PATCH/DELETE /locations/{id}`,
  `PUT/GET /locations/{id}/data`, `/layers` (GET/POST/DELETE, GET `/{id}/data`), `GET /health`, dokumentacja `/docs`.
- `PUT /locations/{id}/data` przyjmuje `data.js` (odcina wrapper `window.CW_DATA[...] = ...;`) lub `data.json`;
  wymaga kluczy `grid, params, payload, zones, pipes`; pilnuje zgodności `id`.

## Pakiet danych lokalizacji (wynik build_location.py, ~3,3 MB dla Portuguese Bend)
- `config` (name, region, hazard, summary, view, pipelines_label), `grid` (wymiary, bounds, mercator, pixel_m,
  `dates`, `series` z progami szumu, `windows`, `windowStats`, korekty), `params` (k_sigma=3, min_zone_px=8,
  dist_near_m=500, ...), `payload` (rastry base64+gzip: `cum` int16 ×10 mm – skumulowane przemieszczenie LOS
  dla każdej daty, `coh` uint8 ×250 – koherencja, `vwin` int16 ×10 – prędkość w oknach mm/rok),
  `zones` (anomalie: geometria, peak/median, trend, szeregi czasowe, pobliskie rurociągi, level),
  `pipes` (37 gazociągów OGIM: linia, profile wzdłuż trasy, ryzyko per seria), `alerts`, `contours`.
- Źródła: NISAR L2 GUNW (NASA/ISRO, ASF DAAC), rurociągi z bazy OGIM. Ruch tylko w linii widzenia satelity (LOS).
- **Opcjonalne rozszerzenia pakietu** (strona używa ich tylko, gdy są): `layers` (dodatkowe rastry:
  `key, label, group, enc i16|u8, scale, down, ramp div|puor|cat|cyclic, time window|pair, classes, unit, range,
  ends, note, popup`), `vectors` (GeoJSON-y: `key, label, kind, style risk|water|alert|profile|gnss, on, time`),
  `grid.fringe_cycle_mm` (włącza tryb „Prążki”), `grid.contour_step_mm`, `config.sources`, `zones[].pin=false`
  (strefa tylko jako obrys, bez pinezki).

## Tabasco (`tabasco_mexico`) – drugie case study
- Dane zespołu: `Dane/TABASCO/tabasco_front` (wyniki QGIS; folder `Dane/` jest w `.gitignore`).
- Import (z `backend/`, ok. 1,5 min, pakiet ~21 MB):
  `python scripts/import_team_results.py "...\Dane\TABASCO\tabasco_front" --id tabasco_mexico --name Tabasco --region "Meksyk (Cárdenas – Villahermosa)" --hazard "Osiadanie terenu nad złożami" --order 2`
- Oś czasu = orbita wznosząca (LOS, 10 dat 18.06–16.10.2026, `los_cum_okres_pary.tif`, brakujące pary
  uzupełnione przez zespół średnim tempem). Strefy = 102 strefy osiadania z obu orbit; `first_index` liczony
  u nas (5. percentyl LOS strefy < −3σ szumu); 20 najpoważniejszych ma pinezki.
- Dodatkowo: ruch pionowy / wschód–zachód (2 orbity), LOS obu orbit, liczba orbit, WorldCover, tempo i klasy
  z monitoringu (okna 36 dni), surowe interferogramy par 12-dniowych, rurociągi z klasą ryzyka, obiekty sieci,
  anomalie wody, alerty (zsynchronizowane z osią), 3 przekroje (wykres w czasie), GNSS VIL2.
- Teksty strony zależą od `hazard` (osuwisko/zbocze vs osiadanie).

## Frontend
- `frontend/index.html` = oryginalna strona od znajomej z zespołu; zmienione tylko wczytywanie danych:
  `fetch('/api/v1/locations')` i `fetch('/api/v1/locations/{id}/data')` (zamiast `locations/index.js` i `data.js`).
  Metadane z backendu (name, region, hazard, summary, view) nadpisują `DATA.config`.
  `window.CW_API` pozwala wskazać API pod innym adresem.
- Serwowana przez backend pod `/` (jeden serwer, bez CORS).
- Od 14.11 UI mocno przerobione (na prośbę użytkownika): układ pełnoekranowy jak Google Earth – stały górny
  panel (logo, nazwa case study, wybór lokalizacji) + oś czasu w stylu „Zdjęcia historyczne” GE, pod spodem
  mapa na cały ekran, legenda pływa w lewym dolnym rogu. Mapa zablokowana na obszar case study
  (`maxBounds` + `minZoom` liczone z `grid.bounds`/`view` danej lokalizacji).
  Usunięte: samouczek, kafelki statystyk, panel anomalii, gazociągi (mapa/legenda), sekcja
  „Jak to zostało obliczone”, stopka. Pokazywany jest tylko pierwszy okres z `grid.series`.
  Popup po kliknięciu w mapę jest pisany dla laika (werdykt, ruch w cm, pewność, mini-wykres historii punktu).
- Anomalie = pinezki z ikoną typu (osuwisko / osiadanie / wypiętrzenie / inny), kolor per typ, legenda typów.
  Dane (`zones[].kind`) znają tylko `down`/`up`, więc typ jest **wyprowadzany**: `up` → wypiętrzenie,
  `down` → osuwisko, jeśli `hazard` lokalizacji wskazuje zbocze/osuwisko, w przeciwnym razie osiadanie.
  Opcjonalne pole `zones[].type` (`landslide|subsidence|uplift|other` lub polskie nazwy) ma pierwszeństwo –
  warto poprosić zespół analizy, żeby je dodawał (`TYPES` w index.html łatwo rozszerzyć o nowe typy).
- Zmiana daty animuje raster (płynne przejście wartości pikseli, ~650 ms, canvas zamiast PNG);
  `prefers-reduced-motion` wyłącza animacje.

## Stan (14.11.2026, wieczór)
- **Pełne uruchomienie potwierdzone** na maszynie użytkownika (Python 3.12.10, `backend/.venv`): import
  3 lokalizacji bez błędów, `/health`, `/api/v1/locations`, pakiet danych Portuguese Bend (11 stref,
  37 gazociągów, 13 dat), `/docs` i strona `/` (zrzut z headless Edge: mapa, anomalie, oś czasu) działają.
  Nie trzeba było nic naprawiać.
- Gotowe dane ma tylko Portuguese Bend; Latrobe Valley i Adelaide Hills są na liście jako „wkrótce”
  (`ready: false`, `/data` zwraca 404 – to zamierzone).
- Uwaga: w Windows PowerShell 5.1 `Invoke-WebRequest` pokazuje polskie znaki z API jako krzaczki –
  to wina klienta (brak charset w nagłówku), bajty są poprawnym UTF-8.
- Uruchomienie: z `backend/` → `python scripts/import_corridor_watch.py "C:\Users\DELL\OneDrive\Desktop\NASA_SPACE_APPS\corridor_watch\corridor_watch\locations"`,
  potem `uvicorn app.main:app --reload`, strona: http://127.0.0.1:8000/

## Pomysły na dalej
- **Zakładki case study** zamiast listy rozwijanej + uogólnienie UI na różne typy obiektów (patrz „Koncepcja”).
- Strona/panel „Dodaj lokalizację” (formularz: metadane + wgranie data.js) – żeby zespół nie używał `/docs`.
- Ulepszenia UI/UX strony; wizualizacja 3D terenu (np. zapadanie się gruntu w czasie).
- Testy (pytest + TestClient), Docker, deploy online dla jury.

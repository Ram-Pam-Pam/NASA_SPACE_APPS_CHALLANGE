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
  frontend/index.html     strona Corridor Watch (Leaflet, vanilla JS)
  Dane/<folder>/data/     gotowe dane dla frontendu od zespołu analizy (meta.json, PNG, GeoJSON) – źródło danych strony
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
- **Opcjonalne rozszerzenia pakietu** (strona używa ich tylko, gdy są): `views` (panel boczny z widokami
  `risk` / `zones` / `phase`, teksty i liczby; gdy jest, znikają ogólne opcje i dolna legenda), `vectors`
  (`style network|risk|profile`), `focus` (wycinek analizy fazowej: prążki u8 + przemieszczenie i16, kontury),
  `display` (`coh_min` = domyślny filtr jakości, `min_mm` = ruch poniżej jest przezroczysty), `layers`
  (rastry jednopasmowe tylko do popupu), `config.sources`, `zones[].pin=false` (strefa tylko jako obrys).

## Tabasco (`tabasco_mexico`) – drugie case study
- Dane zespołu: `Dane/TABASCO/tabasco_front` (wyniki QGIS; folder `Dane/` jest w `.gitignore`).
- Import (z `backend/`, ok. 1,5 min, pakiet ~14 MB):
  `python scripts/import_team_results.py "...\Dane\TABASCO\tabasco_front" --id tabasco_mexico --name Tabasco --region "Meksyk (Cárdenas – Villahermosa)" --hazard "Osiadanie terenu nad złożami" --order 2`
- Oś czasu = orbita wznosząca (LOS, 10 dat 18.06–16.10.2026, `los_cum_okres_pary.tif`, brakujące pary
  uzupełnione przez zespół średnim tempem). Strefy = 102 strefy osiadania z obu orbit; `first_index` liczony
  u nas (5. percentyl LOS strefy < −3σ szumu); 20 najpoważniejszych ma pinezki.
- 15.11 użytkownik zażądał uproszczenia: tylko główne wyniki w panelu bocznym – **Zagrożone rurociągi**
  (domyślny; lista 8 najbardziej zagrożonych z `rurociagi_podsumowanie.json`, klik = przybliżenie), **Strefy
  osiadania**, **Analiza fazowa: złoże Tokal** (`wyniki/qgis_fazy/tokal_*`, 20 m, animowane z osią, kontury,
  3 przekroje z wykresem). Raster: ruch w granicach szumu i słaba koherencja (domyślnie 0,35) przezroczyste.
  Usunięte (na życzenie): ruch pionowy/wschodni jako warstwy, LOS orbit, WorldCover, monitoring, interferogramy
  par, obiekty sieci, anomalie wody, alerty, GNSS. Legenda w panelu opisuje dokładnie to, co widać w danym widoku.
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

## Źródło danych strony (zmiana 14.11.2026, późny wieczór)
- **Strona nie czyta już pakietu `data.js` z `/locations/{id}/data`.** Dane bierze z folderów
  `Dane/<folder>/data/` (opis plików: `Dane/dla_frontendu_portuguese_bend/data/README.md`).
- Backend: `GET /api/v1/datasets` (`app/api/datasets.py`, `services/dataset_registry.py`) wyszukuje
  `Dane/*/data/meta.json` i zwraca `id` (= `meta.aoi`), `name`, `base_url`; pliki są serwowane statycznie
  pod `/datasets/...` (`APP_DATASETS_DIR`, domyślnie `../Dane`). Nowa lokalizacja = nowy folder w `Dane/`.
- Rejestr `/api/v1/locations` jest dla strony opcjonalny: dokłada metadane (region, hazard, view) po `id`
  i pokazuje lokalizacje bez danych jako „wkrótce”.
- Mapowanie na UI: raster = klatki `img/cum_XX.png` (przenikanie między datami zamiast morfowania pikseli);
  tryby: zmiana terenu, prążki (`fringe_XX.png`), tempo (`velocity.png`), pewność (`confidence.png`).
  Odczyt pod kursorem, popup i mini-wykres = `timeseries.json` (siatka `meta.focus`, tylko środek obszaru;
  poza nią mapa pokazuje sam kolor). Próg „istotnego ruchu” = 3 × `meta.noise_mm_yr` przeliczone na okres.
  Anomalie = `active_zone.geojson` (osuwisko, zawsze) + `alerts.geojson` (pinezki w dniu końca okna).
  Warstwy obiektów z klasą zagrożenia (wczytywane po włączeniu): `pipelines`, `critical`, `roads_risk`,
  `buildings_risk`; kolory z `meta.risk_colors`. Podkład `img/relief.png` jako opcja.
- Usunięte z opcji, bo nowe dane tego nie mają: data odniesienia, zakres skali, filtr koherencji, izolinie.
- Sprawdzone 14.11 na macOS (Python 3.12, headless Chrome sterowany przez DevTools): start strony, popup,
  zmiana daty, tryby, warstwy, odtwarzanie – bez błędów w konsoli.

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

## Widok 3D i obiekty uproszczone (zmiana 14.11.2026, noc)
- **Model 3D obszaru: `frontend/view3d.html`** (three.js 0.160 z jsdelivr, OrbitControls). Czyta folder
  `Dane/<folder>/data/` z `meta.json` o `"kind": "scene3d"`: `ground.jpg` (ortofoto RGB jako podłoże),
  `buildings/roads/rail/water/pipelines/areas/places/forest.geojson` (WGS84) i `trees.bin` (drzewa).
  `GET /api/v1/datasets` zwraca teraz pole `kind` (`insar` | `scene3d`).
- **Widok 3D jest domyślny i zastępuje stronę 2D:** lokalizacja, która ma model 3D, otwiera się w
  `view3d.html` (także Portuguese Bend – na prośbę użytkownika model 3D „kompletnie podmienił” starą
  zakładkę). `index.html` pokazuje tylko lokalizacje bez modelu; stara analiza 2D zastąpionej lokalizacji
  jest dostępna pod `index.html?2d#<id>`. Lista „Lokalizacja” nie pokazuje już zastąpionych stron 2D.
- **Przełącznik 2D / 3D** w nagłówku `view3d.html` (`#dims`, wszystkie modele): **2D = ten sam model
  spłaszczony i oglądany z góry** (na prośbę użytkownika 15.11 – nie stara strona analizy). Tryb `FLAT`:
  teren płaski (cieniowanie rzeźby zostaje), płaskie dachy i korony drzew, bez kurtyn stref, północ u góry,
  przeciąganie = przesuwanie. Warstwy, nazwy, legenda i „Przejdź do” działają tak samo. Adres
  `view3d.html?view=2d#<id>` otwiera od razu 2D. Stara strona analizy (`index.html?2d#<id>`) nie jest
  już podlinkowana z przełącznika.
- **Oś czasu ruchu terenu w 2D** (tylko gdy `meta.timeline`, dziś Portuguese Bend; w 3D ukryta):
  23.11.2025 → 22.05.2026, odtwarzanie, poprzedni/następny, przeciąganie po osi. Klatki
  `timeline/frame_XX.png` robi `backend/scripts/build_timeline.py <pakiet InSAR> <pakiet 3D>` (wywoływany też
  z `build_scene3d.py --insar-data`): odczytuje mm z kolorów RdBu klatek `img/cum_XX.png` (sprawdzone z
  `timeseries.json`: mediana różnicy 1 mm), przelicza wiersze z Web Mercatora na siatkę lon/lat i maluje:
  czerwony = w dół, niebieski = w górę, przezroczyste poniżej stałego progu 20 mm dla wszystkich dat (`THRESHOLD_MM` w `build_timeline.py`);
  siła koloru rośnie do 100 mm. Ruch jest w linii widzenia satelity (LOS).
  Legenda: „osiadanie / osuwanie” (czerwony), „wypiętrzanie” (niebieski). **Główne anomalie** = 3 największe
  skupiska zmian z ostatniej daty (łączna zmiana, `ANOMALY_COUNT` w `build_timeline.py`, `meta.timeline.anomalies`):
  numerowane pulsujące znaczniki w 2D, każdy widoczny od daty pojawienia się (≥25% obszaru ponad progiem),
  podpowiedź z powierzchnią, datą i zmianą; klik przybliża. Dziś: 1 osuwisko PB (175 ha, ≥150 mm w dół),
  2 Palos Verdes Estates/Rolling Hills (211 ha, do 88 mm w dół), 3 na zachód od osuwiska (193 ha, do 50 mm w górę).
  Wejście w 2D z osią czasu przybliża na główne osuwisko (`hazardFrame`: największa strefa + części
  przy niej + anomalie do 3 km, z miejscem na otwarty panel warstw) i wyłącza warstwy „Strefa zagrożenia” i „Etykiety” (`AUTO_OFF`); powrót do 3D je przywraca.
- **Sieć energetyczna w 3D/2D** (`power.geojson`, warstwa „Sieć energetyczna”, fiolet): korytarz na ziemi
  z nazwą/napięciem po najechaniu, słupy kratowe i zwisające przewody (w 2D tylko korytarz i znaczniki).
  Portuguese Bend: z pliku zespołu `3d/linie_energetyczne_dociete.gpkg` (115 odcinków; `--power <plik>`),
  w innych AOI z OSM. Tereny przemysłowe są domyślnie włączone.
- **Oś czasu bez migania:** klatki są mieszane w jednym shaderze (dwie tekstury, mieszanie z premultiplikacją),
  a nie dwiema półprzezroczystymi płaszczyznami; tekstury są wgrywane do GPU od razu po pobraniu.
- Model buduje `backend/scripts/build_scene3d.py <aoi.geojson> <Dane/model3d_<id>/data>` (GDAL + numpy +
  polecenie `overturemaps`): OSM przez Overpass, budynki z Overture Maps, ortofoto USGS NAIP (tylko USA),
  drzewa z ESA WorldCover + koloru zdjęcia, wysokości terenu z lidaru USGS 3DEP (`terrain.bin`, tylko USA).
  Modele: Bayou Corne (5 × 5 km) i Portuguese Bend (30 × 30 km, `Dane/model3d_portuguese_bend_california`).
- **Skala miasta (AOI > 10 km):** skrypt zapisuje budynki jako prostokąty w `buildings.bin` (zamiast GeoJSON),
  pomija drogi dojazdowe, bierze zdjęcie 8192 px i siatkę terenu 1025²; widok rysuje budynki instancjami,
  a drogi/tory/rurociągi scala w pojedyncze siatki (klasa `Batch` w `view3d.html`). Morze = płaszczyzna na
  poziomie 0 (`meta.terrain.sea_level_m`).
- **Dane z pakietu InSAR w modelu 3D** (`--insar-data <Dane/.../data>`): tylko przebieg rurociągów OGIM
  (nazwa, operator) i obrys aktywnej strefy (`hazards.geojson`). Na prośbę użytkownika (14.11) model 3D
  **nie pokazuje klas zagrożenia rurociągów ani warstwy ruchu terenu z InSAR** – rurociągi Portuguese Bend
  mają jeden kolor i brak legendy. Dane klas zostają w pakiecie InSAR, gdyby miały wrócić.
- **Uwaga na serwisy USGS (NAIP, 3DEP `exportImage`):** zwracają zamówiony wycinek tylko przy kwadratowych
  pikselach w stopniach; inaczej po cichu poszerzają obszar. Skrypt zamawia kwadratowe piksele i dopasowuje
  wynik do AOI (`fetch_ground`, `fetch_terrain`). Wcześniejszy błąd dawał zdublowane wybrzeże na łączeniach
  kafli i teren rozciągnięty o ok. 20% w Portuguese Bend (naprawione 14.11 w nocy).
- **Strefy zagrożenia w 3D** (osuwisko z `hazards.geojson`, zapadlisko z `water.geojson`, funkcja `hazardZone`
  w `view3d.html`): obrys wygładzony tylko do wyświetlania (Chaikin – bez schodków z pikseli InSAR; dane
  bez zmian), poświata na terenie od krawędzi do środka (czerwony → bursztynowy), cienka linia krawędzi
  i niska „kurtyna” zanikająca ku górze. Wysokości liczone na tych samych trójkątach co siatka terenu
  (`elevAt`), więc wszystko leży dokładnie na powierzchni.
- **Morze w 3D:** płaszczyzna na poziomie 0 z własną teksturą (`seaTexture` w `view3d.html`): przy linii
  brzegowej przezroczysta (widać przybój i baseny portowe ze zdjęcia), od ~200 m od brzegu w pełni kryjąca,
  bo zdjęcie lotnicze na otwartym morzu to pasy nalotu z widocznymi szwami. Kolor jaśniejszy przy brzegu,
  ciemniejszy dalej, z lekką falą.
- **Podpowiedzi po najechaniu** tylko dla wybranych obiektów z nazwą: rurociągi, strefy zagrożenia, nazwane
  wody, drogi z numerem lub główne z nazwą, nazwane tory, tereny przemysłowe, budynki i maszty. Ocean,
  nienazwane ulice i budynki nic nie pokazują. Trafienie liczy się tylko, gdy nie leży za/pod terenem
  (`groundDistance` – marsz wzdłuż promienia po funkcji wysokości), więc ląd nie podpisuje się jako ocean.
- **Rzeźba terenu w 3D:** Bayou Corne ma realnie 3,2 m różnicy wysokości, więc widok mnoży wysokości
  (`meta.terrain.default_exaggeration`, przełącznik ×1/×5/×15/×30; obiekty są przebudowywane na nowym terenie).
  Wody z OSM są w skrypcie obniżane do poziomu lustra wody (lidar jest starszy niż zapadlisko).
- **Obiekty uproszczone na mapie InSAR** (`index.html`, grupa „Model uproszczony”): bryły budynków
  (`buildings_3d.bin` + `.json`, dach w kolorze zagrożenia), tory z podkładami (`rail_3d.geojson`), linie
  energetyczne ze słupami (`power_3d.geojson`). Pliki robi `backend/scripts/build_symbols.py 3d <data>`
  z warstw w folderze `3d/` (duże pliki źródłowe, nie commitować).

## Szczegóły Portuguese Bend i sygnał do weryfikacji (15.11.2026)
- **`backend/scripts/build_scene_detail.py`** (uruchamiany też z `build_scene3d.py` dla AOI > 10 km z `--cache`):
  98 kafli zdjęcia NAIP ~1,2 m (`ground_tiles/<r>_<c>.jpg`, 45 MB, ładowane w widoku tylko w pobliżu kamery,
  max 16 naraz), drzewa z podczerwieni NAIP (NDVI + ciemność/szorstkość korony; 178 tys.), prawdziwe obrysy
  budynków z kolorem dachu ze zdjęcia (`buildings_fp.bin`, 9 MB; zastępuje `buildings.bin`). Pobieranie
  idzie równolegle (5 kafli naraz) – serwis NAIP odpowiada ~30 s na zapytanie.
- **Klik na obiekt → panel „Sygnał do weryfikacji”** (`verify` w `view3d.html`, tylko gdy lokalizacja ma też
  pakiet InSAR o tym samym id). Dopasowanie do wyników zespołu: budynek → `buildings_risk` (wewnątrz/≤12 m),
  rurociąg → `pipelines` (≤120 m, przebiegi schematyczne), linia energetyczna/kolej → `critical`, droga →
  `roads_risk`; klik obok cienkiej linii dociąga do niej (~10 px). Budynek/droga spoza list i z serią pomiarów
  → „brak sygnału ponad szum” (listy zawierają tylko narażone + kontekst przy strefie).
  Klasy pokazane jako sygnał: wysokie = „silny sygnał – priorytet weryfikacji”, średnie = „umiarkowany”,
  niskie = „brak sygnału ponad szum”, brak danych = „brak wiarygodnego pomiaru”. Zawsze trzy zastrzeżenia:
  klasa = ruch gruntu pod obiektem (nie stan konstrukcji), pomiar w LOS, tempo z ~6 miesięcy (pora deszczowa).
- **Panel jest napisany dla laika** (prośba 15.11): na wierzchu tylko werdykt („Grunt pod tym budynkiem
  wyraźnie/lekko się przesuwa / jest spokojny”), jedno zdanie ile i w którą stronę (cm, „obniżył się lub
  zsunął” – LOS, więc z „lub”), czy ruch był stały, co zrobić, mały wykres z datami. Liczby, klasy,
  uzasadnienie i zastrzeżenia techniczne są w zwiniętym „Szczegóły dla specjalistów”.
- **Sezonowość – rozwiązanie w UI:** „Czy to trwały ruch?” – seria z najsilniej poruszającego się piksela
  (3×3 × 100 m) i test: tempo w 1. i 2. połowie okresu + odsetek 12-dniowych kroków w kierunku trendu →
  „narasta w obu połowach” / „skupiony w części okresu (możliwa pora deszczowa)” / „zmienił kierunek”;
  plus `okna_ruch` (≥2 = ruch powtarza się w kolejnych oknach). **Docelowo (zespół analizy):** seria ≥ 12
  miesięcy i model trend + sinusoida roczna; porównanie tej samej pory roku rok do roku; historia z
  Sentinel-1 (np. produkt OPERA DISP-S1) i orbity wznoszące+zstępujące do rozbicia LOS na pion/wschód.

## Pusta zakładka „Nowa lokalizacja” (15.11.2026)
- `Dane/model3d_nowa_lokalizacja/data/meta.json` z `"empty": true` → `/api/v1/datasets` zwraca `ready: false`,
  lista pokazuje „Nowa lokalizacja · w przygotowaniu” (na końcu), strona pokazuje komunikat zamiast modelu.
- Wypełnienie: zmienić `aoi`/`name` w meta.json i zbudować model `build_scene3d.py <aoi.geojson>
  Dane/model3d_nowa_lokalizacja/data [--insar-data …]` – skrypt nadpisze meta.json pełną wersją (bez `empty`).
  Oś czasu 2D i panel „Sygnał do weryfikacji” pojawią się, gdy będzie pakiet InSAR o tym samym `aoi`.

## Tabasco (Meksyk) – pusta mapa 2D/3D (15.11.2026)
- `Dane/model3d_tabasco_mexico/data` (AOI `tabasco_mexico.geojson`, 106 × 90 km, Cárdenas – Villahermosa –
  Paraíso): na razie tylko zdjęcie i teren, warstwy obiektów puste. Zbudowane
  `build_scene3d.py ../tabasco_mexico.geojson Dane/model3d_tabasco_mexico/data --name "Tabasco" --ground-only --cache …`.
- Poza USA skrypt bierze: zdjęcie **Sentinel-2 cloudless 2024 (EOX, 10 m, CC BY-NC-SA 4.0 – licencja
  niekomercyjna)** z kafli WMTS (pobieranie po 3 naraz z ponowieniami – serwer zwraca 403 przy szybkim
  pobieraniu), teren **Copernicus DEM GLO-30** (model powierzchni: zawiera drzewa i budynki). Wykrywanie
  USA: `in_usa(bbox)`. Morze wykryte z DEM (≤ 0,5 m), bo bez OSM nie ma linii brzegowej.
- Dodanie obiektów później: ten sam skrypt bez `--ground-only` (OSM + Overture; poza USA bez kafli NAIP
  i drzew z podczerwieni – `build_scene_detail.py` działa tylko w USA).
- Widok 2D: na płaskiej mapie wszystko leżące na ziemi jest podniesione o stałe `FLAT_LIFT` (zamiast cm),
  bo przy kamerze 100 km nad ziemią warstwy migotały; płyta pod modelem jest w 2D ukryta.

## Tabasco w pełnej witrynie zespołu (`view3d.html`, 15.11.2026, scalenie gałęzi `test-polaczone` + `main`)
- Bazą jest witryna zespołu (`frontend/view3d.html` + `index.html`); nasza wersja Tabasco z `main` (prototyp
  w `index.html`) jest zachowana jako **`frontend/analiza.html`** (niepodlinkowana, działa z `/api/v1/locations`;
  zapas, można usunąć). Dane Tabasco są teraz w formacie modelu zespołu: `Dane/model3d_tabasco_mexico/data`.
- **`backend/scripts/build_tabasco_scene.py <Dane/TABASCO/tabasco_front> <Dane/model3d_tabasco_mexico/data>`**
  (~20 s; wymaga `requirements-tools.txt`; korzysta z `import_team_results.py`) zamienia wyniki QGIS zespołu na
  warstwy modelu: `pipelines.geojson` (2344 odcinki sieci OGIM jako `risk: niskie|brak danych` + 66 zagrożonych
  z `risk, reason, v, gradient, series_mm…`), `hazards.geojson` (102 strefy; 20 z nazwą/pinezką, reszta
  `label:false` = sam obrys), `places.geojson`, `timeline/` (10 klatek LOS orbity wznoszącej, próg 35 mm,
  koherencja ≥ 0,35), `phase/` (analiza fazowa złoża Tokal: 10 klatek prążków 121 mm + `contours.geojson` co
  20 mm), `extras.json` (3 przekroje z profilem w czasie + stacja GNSS VIL2) oraz w `meta.json`: `timeline`
  (+ `anomalies` z `first_date`, `note`, `noise_mm`, `phase`), `summary` (statystyki, legenda klas, uwagi), `extras`.
  Pinezka strefy pojawia się w dniu, od którego jej sygnał stale przekracza próg.
- Rozszerzenia `view3d.html` (data-driven – włączają się tylko, gdy meta/dane je mają; PB i Bayou Corne bez zmian):
  - **Rurociągi w trybie ryzyka** (`RISK_MODE`, gdy któryś odcinek ma `risk`): kolory klas z `META.summary.legend`
    zamiast „co płynie”; sieć i zagrożone odcinki rysowane jako **grube linie o stałej szerokości w pikselach**
    (`fatLines`, `LineSegments2` – widoczne też z całym obszarem w kadrze), zagrożone dodatkowo poświata i w 3D rurki.
    Podpowiedź i klik dla linii liczone własnym testem odległości (`extras.tipAt/click`), nie raycasterem.
  - **Blok „Wyniki analizy”** (`META.summary`, zwinięty) w panelu warstw; legenda klas w sekcji rurociągów.
  - **Oś czasu w 2D:** przełącznik „Zmiana terenu / Analiza fazowa” (gdy `timeline.phase`); tryb fazowy = prążki +
    kontury z opisami (mm) dla wybranej daty, kamera leci nad Tokal; nowe pinezki w danym pomiarze są limonkowe
    (`.amark.fresh`). Start 2D z `timeline.phase` otwiera się na obszarze analizy fazowej (`hazardFrame`).
  - **Klik:** rurociąg → panel „dla laika” (klasa, ruch pionowy, powód, wykres LOS z pasmem szumu), przekrój →
    profil ruchu wzdłuż linii (krzywa na każdą datę, przecięcia z zagrożonymi rurociągami), stacja GNSS (znacznik
    „G”) → porównanie GNSS ↔ NISAR z wykresem. Warstwa „Przekroje i stacja GNSS” (`extras`).
  - Panel „Sygnał do weryfikacji” (`verify`) wymaga pakietu InSAR o tym samym id – dla Tabasco go nie ma
    (jego rolę przejął panel `extras`).
- Test: Playwright + Edge z flagami `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader
  --ignore-gpu-blocklist` (bez nich WebGL nie działa w headless).
- Po scaleniu trzeba **zrestartować serwer** (stary proces z `main` nie zna `/api/v1/datasets`).

## Tabasco – obiekty z otwartych danych i wygląd 3D (15.11.2026)
- **`backend/scripts/build_tabasco_objects.py <Dane/model3d_tabasco_mexico/data> --cache data/cache_tabasco`**
  (uruchamiać PO `build_tabasco_scene.py`; ~5 min; bez GDAL, działa na Windows). Źródła: **Overture Maps**
  (budynki, drogi, kolej, linie energetyczne, woda, tereny przemysłowe, miejscowości – dane OSM) i **ESA WorldCover**
  (drzewa, laguny). Overpass odpadł: na obszarze 106 × 90 km zwracał 504/timeouty. Pliki Overture pobiera się
  raz do cache (`overturemaps download --bbox=-93.83,17.7,-92.83,18.51 -f geojsonseq -t <typ>`, typy w docstringu).
- Wydajność: wszystko w promieniu `FOCUS_KM` = 14 km od `meta.home` (złoże Tokal), dalej tylko budynki ≥ 250 m²,
  bez ulic osiedlowych, rzadkie drzewa (34 m vs 180 m). Wynik: 189 tys. budynków (`buildings.bin`), 25 tys. dróg,
  223 tys. drzew, 691 wód, 140 linii energetycznych, 129 miejscowości. Teren spłaszczony pod 496 lagunami (DEM to
  model powierzchni – nad wodą były „fale”).
- `view3d.html`: `meta.home` = start widoku 3D (lon, lat, dist_m, polar_deg); przy zdjęciu ≥ 5 m/px drobna
  szczegółowość jasności podłoża z bliska (shader w `groundMat`); nazwy wsi w dużych scenach widoczne z ≤ 14 km.
- Ograniczenie: zdjęcie to Sentinel-2 10 m (brak otwartych ortofotomap jak NAIP poza USA), więc z bliska teren
  jest bardziej rozmyty niż w Bayou Corne.

## Tabasco – skupienie na zagrożonych rurociągach (15.11.2026, prośba użytkownika; TYLKO Tabasco)
- Zmiany dotyczą wyłącznie Tabasco: w `view3d.html` włączają je pola w `meta.json` Tabasco (`hide_empty_layers`,
  `hazard_colors`, `place_label_m`, `home`, `timeline.points`, `timeline.anomaly_label`, klasy `risk` w rurociągach).
  **Pozostałych lokalizacji nie zmieniać** (użytkownik: „nie zmieniaj nic w pozostałych, tylko Tabasco”).
- Usunięte warstwy: budynki, drogi, tory, sieć energetyczna, drzewa, tereny przemysłowe, woda
  (`build_tabasco_objects.py` robi je tylko z `--full`; domyślnie tylko nazwy miejscowości i wygładzenie terenu pod wodą).
- Rurociągi: osobne przełączniki – wysokie (czerwony), średnie (pomarańczowy), niskie (zielony #37b24d) na szarej
  „Sieci rurociągów (całej)”; ciemna obwódka, żeby było je widać na prążkach. Strefy osiadania fioletowe, bez etykiet
  (`label:false` dla wszystkich; nazwa tylko w podpowiedzi).
- Znaczniki na osi czasu = **10 największych osiadań pod rurociągami** (zagrożone odcinki wg ruchu pionowego,
  co najmniej 1,5 km od siebie, z widocznym spadkiem na osi), nie największe anomalie; klik = karta rurociągu.
  Ta sama lista jest w panelu warstw zamiast „Przejdź do” (ranking „Największe osiadania pod rurociągami”: numer,
  nazwa, mm/rok; klik = przelot + karta rurociągu). Inne lokalizacje mają nadal „Przejdź do”.
- Klik w dowolne miejsce mapy = **lokalny pomiar** z `points.bin` (siatka ~250 m: seria LOS, ruch pionowy ± niepewność,
  pewność danych) z oceną dla laika wg tych samych progów co klasy rurociągów i najbliższym rurociągiem.
- Analiza fazowa: maks. 8 etykiet konturów (najgłębsze poziomy, min. 150 px od siebie, tylko na obrazie prążków),
  klucz klas rurociągów w pasku osi czasu.

## Pomysły na dalej
- **Zakładki case study** zamiast listy rozwijanej + uogólnienie UI na różne typy obiektów (patrz „Koncepcja”).
- Strona/panel „Dodaj lokalizację” (formularz: metadane + wgranie data.js) – żeby zespół nie używał `/docs`.
- Ulepszenia UI/UX strony; wizualizacja 3D terenu (np. zapadanie się gruntu w czasie).
- Testy (pytest + TestClient), Docker, deploy online dla jury.

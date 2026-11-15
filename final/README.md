# Corridor Watch – wersja końcowa (Tabasco, Portuguese Bend, Bayou Corne)

Wszystko, czego potrzebuje strona Corridor Watch dla trzech lokalizacji: backend, strona, dane gotowe do
wyświetlenia oraz dane źródłowe i skrypty, z których je zbudowano. Lokalizację zmienia się listą w nagłówku strony.

## Uruchomienie

Potrzebny jest Python 3.11 lub nowszy.

```bash
./start.sh
```

Jeśli port 8000 jest zajęty (np. działa serwer z głównego projektu), skrypt to zgłosi; wtedy
`PORT=8001 ./start.sh` i adresy z `:8001`. Po zmianie danych odśwież stronę z pominięciem pamięci
przeglądarki (Cmd + Shift + R), bo przeglądarka trzyma stare klatki osi czasu.

Skrypt przy pierwszym uruchomieniu tworzy środowisko `backend/.venv` i instaluje zależności z
`backend/requirements.txt` (FastAPI, uvicorn). Potem otwórz:

| Lokalizacja | Widok 3D | Widok 2D (z osią czasu, gdzie jest) |
|---|---|---|
| Tabasco (Meksyk) | http://127.0.0.1:8000/view3d.html#tabasco_mexico | http://127.0.0.1:8000/view3d.html?view=2d#tabasco_mexico |
| Portuguese Bend (Kalifornia) | http://127.0.0.1:8000/view3d.html#portuguese_bend_california | http://127.0.0.1:8000/view3d.html?view=2d#portuguese_bend_california |
| Bayou Corne (Luizjana) | http://127.0.0.1:8000/view3d.html#bayou_corne_louisiana | http://127.0.0.1:8000/view3d.html?view=2d#bayou_corne_louisiana |

Panel po kliknięciu obiektu działa w Tabasco i Portuguese Bend (tam są wyniki InSAR); Bayou Corne ma sam model.

Ręcznie, bez skryptu:

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

Backend trzeba uruchamiać z folderu `backend`: stronę bierze z `../frontend`, a dane z `../Dane`.
Biblioteka 3D (three.js 0.160) jest w `frontend/vendor`, więc strona działa bez internetu. Z sieci
pobierana jest tylko czcionka (bez niej strona użyje czcionki systemowej).

## Zawartość

| Folder | Co zawiera |
|---|---|
| `backend/app` | serwer FastAPI: lista lokalizacji `/api/v1/datasets`, pliki danych pod `/datasets/...`, strona pod `/` |
| `backend/scripts` | skrypty, którymi zbudowano dane (niżej) |
| `frontend/view3d.html` | strona: model 3D, widok 2D z osią czasu, warstwy, panel po kliknięciu obiektu |
| `frontend/index.html` | strona startowa, przekierowuje do `view3d.html` |
| `Dane/model3d_tabasco_mexico/data` | model: zdjęcie Sentinel-2, teren FABDEM, budynki, drzewa, drogi, rurociągi, tereny przemysłowe, oś czasu A062 / D156 z anomaliami przy rurach |
| `Dane/analiza_tabasco_mexico/data` | dane panelu po kliknięciu: szereg czasowy obu orbit (kafle), klasy budynków, dróg i rurociągów |
| `Dane/model3d_portuguese_bend_california/data` | model PB: zdjęcia NAIP (kafle ~1,2 m), lidar 3DEP, budynki, drzewa, drogi, tory, rurociągi, sieć energetyczna, mury oporowe i skarpy, strefa osuwiska, oś czasu |
| `Dane/dla_frontendu_portuguese_bend/data` | wyniki InSAR PB (zespół): klasy budynków i infrastruktury, szereg czasowy, klatki przemieszczeń – panel po kliknięciu i oś czasu |
| `Dane/model3d_bayou_corne_louisiana/data` | model Bayou Corne: zdjęcie NAIP, lidar, budynki, las, drogi, rurociągi, zapadlisko |
| `dane_zrodlowe/` | obszary AOI (`tabasco_mexico.geojson`, `portuguese_bend_california.geojson`, `bayou_corne_louisiana.geojson`), rurociągi OGIM Tabasco (`tabasco_ogim.gpkg`), sieć energetyczna PB (`linie_energetyczne_dociete.gpkg`), kostki przemieszczeń NISAR Tabasco `A062_cube.npz` / `D156_cube.npz`, `products.json`, `monitoring.json`, ocena rurociągów `dashboard_data.js` |

## Przebudowa danych (opcjonalnie)

Potrzebne: Python z GDAL (bindingi `osgeo`) i NumPy, np. Homebrew `python3`, oraz `pip install overturemaps`
(budynki). Polecenia uruchamia się z folderu `final`.

1. Model 3D (pobiera OSM, Overture, Sentinel-2, FABDEM, ESA WorldCover; `--cache` = folder na pobrane pliki):

   ```bash
   python3 backend/scripts/build_scene3d.py dane_zrodlowe/tabasco_mexico.geojson Dane/model3d_tabasco_mexico/data \
       --name "Tabasco" --layers roads,places,buildings,trees,pipelines,areas \
       --road-kinds "motorway|trunk|primary|secondary" \
       --pipelines dane_zrodlowe/tabasco_ogim.gpkg:Oil_Natural_Gas_Pipelines --pipes-one-class --pipes-thin \
       --cache cache_tabasco
   ```

2. Oś czasu A062 / D156 i anomalie przy rurociągach (z kostek):

   ```bash
   python3 backend/scripts/build_tabasco_timeline.py dane_zrodlowe/zrodla_tabasco Dane/model3d_tabasco_mexico/data
   ```

3. Dane panelu po kliknięciu:

   ```bash
   python3 backend/scripts/build_tabasco_analysis.py dane_zrodlowe/zrodla_tabasco Dane/model3d_tabasco_mexico/data Dane/analiza_tabasco_mexico/data
   ```

Kolejność ma znaczenie: krok 1 nadpisuje `meta.json` modelu, więc krok 2 trzeba po nim powtórzyć.

Portuguese Bend (oś czasu i strefa osuwiska z pakietu InSAR zespołu, potem mury oporowe i skarpy):

```bash
python3 backend/scripts/build_scene3d.py dane_zrodlowe/portuguese_bend_california.geojson Dane/model3d_portuguese_bend_california/data \
    --name "Portuguese Bend" --insar-data Dane/dla_frontendu_portuguese_bend/data --hazard-name "Osuwisko Portuguese Bend" \
    --power dane_zrodlowe/linie_energetyczne_dociete.gpkg --cache cache_pb
python3 backend/scripts/build_slopes.py Dane/model3d_portuguese_bend_california/data \
    --insar-data Dane/dla_frontendu_portuguese_bend/data --cache cache_pb
```

Bayou Corne:

```bash
python3 backend/scripts/build_scene3d.py dane_zrodlowe/bayou_corne_louisiana.geojson Dane/model3d_bayou_corne_louisiana/data \
    --name "Bayou Corne Sinkhole" --cache cache_bayou
```

Modele USA (PB, Bayou) biorą zdjęcia NAIP i lidar USGS 3DEP; PB jest duży (30 km), więc skrypt dobudowuje
kafle szczegółowe, drzewa z podczerwieni i obrysy budynków (`build_scene_detail.py`). Wyniki InSAR PB
(`Dane/dla_frontendu_portuguese_bend`) przygotował zespół analizy – tu są gotowe, bez skryptu.

## Jak czytać wyniki

- Wartości to przemieszczenie w linii widzenia satelity (LOS), nie ruch pionowy. Ujemne = od satelity.
- Okresy: Tabasco czerwiec–październik 2026, Portuguese Bend listopad 2025 – maj 2026 (w obu pora deszczowa);
  część zmian może być sezonowa, a w Tabasco, zwłaszcza na mokradłach, pochodzić od wody.
- Klasa obiektu opisuje ruch gruntu pod nim (sygnał do sprawdzenia), nie stan konstrukcji.

## Źródła i licencje

Sentinel-2 cloudless 2024 (EOX, CC BY-NC-SA 4.0) i FABDEM V1-2 (CC BY-NC-SA 4.0) – tylko do użytku
niekomercyjnego (Tabasco). USGS NAIP i USGS 3DEP (domena publiczna, USA), OpenStreetMap (ODbL), Overture Maps,
ESA WorldCover 2021 (CC BY 4.0), OGIM, NISAR L2 GUNW (NASA/ISRO).

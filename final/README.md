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

Panel po kliknięciu obiektu działa w Tabasco (karta rurociągu, przekroje, stacja GNSS, lokalny pomiar w dowolnym
miejscu) i Portuguese Bend (wyniki InSAR); Bayou Corne ma sam model.

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
| `Dane/model3d_tabasco_mexico/data` | Tabasco (zespół, gałąź `tabasco_final_version`): rurociągi OGIM z klasami zagrożenia, strefy osiadania, oś czasu orbity wznoszącej z 10 największymi osiadaniami pod rurociągami, analiza fazowa złoża Tokal (`phase/`), przekroje i stacja GNSS (`extras.json`), lokalne pomiary (`points.bin`), nazwy miejscowości |
| `Dane/model3d_portuguese_bend_california/data` | model PB: zdjęcia NAIP (kafle ~1,2 m), lidar 3DEP, budynki, drzewa, drogi, tory, rurociągi, sieć energetyczna, mury oporowe i skarpy, strefa osuwiska, oś czasu |
| `Dane/dla_frontendu_portuguese_bend/data` | wyniki InSAR PB (zespół): klasy budynków i infrastruktury, szereg czasowy, klatki przemieszczeń – panel po kliknięciu i oś czasu |
| `Dane/model3d_bayou_corne_louisiana/data` | model Bayou Corne: zdjęcie NAIP, lidar, budynki, las, drogi, rurociągi, zapadlisko |
| `dane_zrodlowe/*.geojson` | obszary AOI Portuguese Bend i Bayou Corne (AOI Tabasco: `Dane/model3d_tabasco_mexico/data/aoi.geojson`) |
| `dane_zrodlowe/portuguese_bend_insar_nisar/` | surowe pary interferometryczne NISAR (24 GeoTIFF, track 013D, ramka 071) – źródło wyników InSAR Portuguese Bend |
| `dane_zrodlowe/warstwy_zespolu_pb/` | warstwy zespołu dla PB: budynki, tory, linie energetyczne (użyte w modelu 3D), rurociągi OGIM |
| `dane_zrodlowe/pobrane/` | pobrane dane, z których zbudowano modele PB i Bayou (OSM, Overture, zdjęcia NAIP, lidar 3DEP, WorldCover) – z nimi przebudowa działa bez pobierania |

## Przebudowa danych (opcjonalnie)

Polecenia uruchamia się z folderu `final`.

Tabasco zbudowano skryptami zespołu z wyników QGIS (`Dane/TABASCO/tabasco_front`, nie ma ich w tym folderze;
potrzebne biblioteki: `backend/requirements-tools.txt`; tych wyników nie ma w paczce – ma je autor wersji Tabasco):

```bash
python3 backend/scripts/build_tabasco_scene.py <Dane/TABASCO/tabasco_front> Dane/model3d_tabasco_mexico/data
python3 backend/scripts/build_tabasco_objects.py Dane/model3d_tabasco_mexico/data --cache cache_tabasco
```

Modele PB i Bayou: Python z GDAL (bindingi `osgeo`) i NumPy, np. Homebrew `python3`, oraz `pip install overturemaps`.

Portuguese Bend (oś czasu i strefa osuwiska z pakietu InSAR zespołu, potem mury oporowe i skarpy):

```bash
python3 backend/scripts/build_scene3d.py dane_zrodlowe/portuguese_bend_california.geojson Dane/model3d_portuguese_bend_california/data \
    --name "Portuguese Bend" --insar-data Dane/dla_frontendu_portuguese_bend/data --hazard-name "Osuwisko Portuguese Bend" \
    --power dane_zrodlowe/warstwy_zespolu_pb/linie_energetyczne_dociete.gpkg --cache dane_zrodlowe/pobrane/portuguese_bend
python3 backend/scripts/build_slopes.py Dane/model3d_portuguese_bend_california/data \
    --insar-data Dane/dla_frontendu_portuguese_bend/data --cache dane_zrodlowe/pobrane/portuguese_bend
```

Bayou Corne:

```bash
python3 backend/scripts/build_scene3d.py dane_zrodlowe/bayou_corne_louisiana.geojson Dane/model3d_bayou_corne_louisiana/data \
    --name "Bayou Corne Sinkhole" --cache dane_zrodlowe/pobrane/bayou_corne
```

Modele USA (PB, Bayou) biorą zdjęcia NAIP i lidar USGS 3DEP; PB jest duży (30 km), więc skrypt dobudowuje
kafle szczegółowe, drzewa z podczerwieni i obrysy budynków (`build_scene_detail.py`). Wyniki InSAR PB
(`Dane/dla_frontendu_portuguese_bend`) przygotował zespół analizy – tu są gotowe, bez skryptu.

## Jak czytać wyniki

- Wartości to przemieszczenie w linii widzenia satelity (LOS), nie ruch pionowy. Ujemne = od satelity.
- Okresy: Tabasco czerwiec–październik 2026 (orbita wznosząca), Portuguese Bend listopad 2025 – maj 2026 (w obu
  pora deszczowa); część zmian może być sezonowa, a w Tabasco, zwłaszcza na mokradłach, pochodzić od wody.
- Klasa obiektu opisuje ruch gruntu pod nim (sygnał do sprawdzenia), nie stan konstrukcji.

## Źródła i licencje

Sentinel-2 cloudless 2024 (EOX, CC BY-NC-SA 4.0) – tylko do użytku niekomercyjnego (Tabasco); Copernicus DEM GLO-30. USGS NAIP i USGS 3DEP (domena publiczna, USA), OpenStreetMap (ODbL), Overture Maps,
ESA WorldCover 2021 (CC BY 4.0), OGIM, NISAR L2 GUNW (NASA/ISRO).

## Użycie narzędzi SI (Super Intelligence)

Zgodnie z zasadami NASA Space Apps Challenge informujemy, gdzie i jak użyliśmy narzędzi SI. Ta sama informacja
jest w metadanych: plik `si_usage.json`, klucz `si_disclosure` w każdym `Dane/*/data/meta.json`, znacznik
`<meta name="si-disclosure">` na stronie oraz opis API (`/docs`).

**Narzędzia:** Claude Code i Claude (claude.ai) firmy Anthropic, ChatGPT firmy OpenAI.

| Część projektu | Jak użyto SI |
|---|---|
| Kod strony (`frontend/`) | kod modelu 3D, widoku 2D, osi czasu i paneli pisany z pomocą SI (Claude Code, Claude) |
| Backend (`backend/app`) | serwer FastAPI pisany z pomocą SI |
| Skrypty danych (`backend/scripts`) | skrypty budujące modele 3D, oś czasu, wygładzanie klatek i import wyników zespołu pisane z pomocą SI |
| Analiza InSAR i teksty | pomoc SI przy kodzie analizy danych NISAR, opisach w interfejsie i dokumentacji (w tym ten plik) |
| GIF-y (`gify_pitch/`) | nagrania ekranu działającej aplikacji; agent SI pomagał je nagrać i obrobić (przycięcie, podpisy). Obraz pochodzi z aplikacji, nie z modelu generującego obrazy |
| Dane (`Dane/`) | pliki zbudowane skryptami napisanymi z pomocą SI |

**Czego SI nie generowało.** Pomiary nie są wygenerowane przez SI. Ruch gruntu pochodzi z prawdziwych danych
satelitarnych NISAR (NASA/ISRO). Zdjęcia, ukształtowanie terenu i infrastruktura pochodzą ze źródeł wymienionych
w sekcji „Źródła i licencje”. SI pomagało pisać kod, który te dane przetwarza i pokazuje. Wyniki sprawdzał zespół.

**Marka NASA.** Treści przygotowane z pomocą SI nie używają, nie zawierają i nie przerabiają logo, flag, insygniów
misji ani innych elementów marki NASA. Nazwy NASA i NISAR występują tylko jako tekstowe wskazanie źródła danych.

*In English:* Super Intelligence (SI) tools – Claude Code and Claude (Anthropic), ChatGPT (OpenAI) – were used to
help write the frontend, the FastAPI backend, the data-processing scripts, the InSAR analysis code, the interface
texts and this documentation, and to record and edit the pitch GIFs (screen recordings of the real application,
not generated imagery). The ground-motion measurements are real NISAR satellite data (NASA/ISRO), not SI-generated. No NASA
logos, flags, mission insignia or other NASA branding are used in SI-generated content. Machine-readable
disclosure: `si_usage.json`.

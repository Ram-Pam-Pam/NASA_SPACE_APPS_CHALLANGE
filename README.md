# Corridor Watch – NASA Space Apps 2026 („Dancing with the SARs”)

Ruch gruntu z radaru satelitarnego (NISAR InSAR) przy gazociągach i rurociągach.

```
Projekt/
  backend/    FastAPI: przechowuje lokalizacje i ich pakiety danych, serwuje stronę
  frontend/   strona Corridor Watch (index.html), dane bierze z API
```

## Uruchomienie (Windows, PowerShell)

```powershell
cd backend
python -m venv .venv                 # tylko za pierwszym razem
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- Strona: http://127.0.0.1:8000/
- API i dokumentacja (można klikać): http://127.0.0.1:8000/docs

## Dane lokalizacji

Analizę liczy `build_location.py` (repo Corridor Watch, wymaga GDAL). Wynik, czyli `data.js`,
trafia do backendu na jeden z dwóch sposobów:

1. **Skrypt (wszystkie lokalizacje naraz)**, z folderu `backend`:
   ```powershell
   python scripts/import_corridor_watch.py "C:\...\corridor_watch\locations"
   ```
   Można uruchamiać wielokrotnie – odświeża metadane i dane. Serwer nie wymaga restartu.
2. **Przez API** (`/docs`):
   - `POST /api/v1/locations` – nowa lokalizacja (bez danych = „wkrótce” na liście),
   - `PUT /api/v1/locations/{id}/data` – wgranie `data.js` albo `data.json`,
   - `PATCH /api/v1/locations/{id}` – zmiana nazwy, opisu, kolejności, widoku mapy.

Dane trzymane są w `backend/data/` (poza Gitem).

### Ochrona zapisu

Odczyt (GET) jest publiczny. Dodawanie, zmiana i usuwanie danych (POST / PATCH / PUT / DELETE) wymaga
tokenu z `APP_ADMIN_TOKEN` (plik `backend/.env`), wysłanego w nagłówku `X-Admin-Token`
(w `/docs`: przycisk **Authorize**). Bez ustawionego tokenu zmiany przez API działają tylko z tego
samego komputera (localhost), więc serwer publiczny jest domyślnie tylko do odczytu.
Skrypty importu zapisują dane bezpośrednio i tokenu nie potrzebują.

## API

| Metoda | Adres | Opis |
|---|---|---|
| GET | `/api/v1/locations` | lista lokalizacji (gotowe pierwsze) |
| GET | `/api/v1/locations/{id}` | metadane lokalizacji |
| POST | `/api/v1/locations` | nowa lokalizacja |
| PATCH | `/api/v1/locations/{id}` | zmiana metadanych |
| PUT | `/api/v1/locations/{id}/data` | wgranie / podmiana pakietu danych |
| GET | `/api/v1/locations/{id}/data` | pakiet danych (to rysuje strona) |
| DELETE | `/api/v1/locations/{id}` | usunięcie lokalizacji z danymi |
| GET/POST/DELETE | `/api/v1/layers...` | dodatkowe warstwy GeoJSON |
| GET | `/health` | czy serwer działa |

## Użycie narzędzi SI / SI disclosure

**PL.** Zgodnie z zasadami NASA Space Apps oznaczamy, gdzie użyliśmy narzędzi SI (Super Intelligence):

- **Kod:** backend (FastAPI), strona (`frontend/index.html`, `frontend/view3d.html`) i skrypty przetwarzania
  danych (`backend/scripts/`) powstały z pomocą narzędzi SI (Anthropic Claude): generowanie i poprawianie kodu,
  przegląd bezpieczeństwa, wdrożenie na serwer.
- **Dane:** dane pomiarowe i zdjęcia są prawdziwe (NISAR, USGS NAIP i 3DEP, Sentinel-2, FABDEM, OpenStreetMap,
  Overture Maps, OGIM) – SI ich nie generowała. Gotowe pakiety w `Dane/` zostały wyliczone skryptami napisanymi
  z pomocą SI; ich pliki `meta.json` mają pole `si_disclosure`.
- **Oznaczenia:** strona ma dyskretny znak „SI-assisted” w prawym dolnym rogu i metadane `si-disclosure`
  w nagłówku HTML; API ma tę informację w opisie (`/docs`).
- SI nie generowała obrazów, wideo ani dźwięku i nie używa elementów identyfikacji wizualnej NASA.

**EN.** As required by NASA Space Apps, SI (Super Intelligence) use is disclosed:

- **Code:** the backend (FastAPI), the web page and the data-processing scripts were written with the help of SI tools
  (Anthropic Claude): code generation and editing, security review, deployment.
- **Data:** measurement data and imagery are real sources (NISAR, USGS NAIP and 3DEP, Sentinel-2, FABDEM,
  OpenStreetMap, Overture Maps, OGIM), not SI-generated. The ready-made packages in `Dane/` were computed by
  SI-assisted scripts; their `meta.json` files carry a `si_disclosure` field.
- **Markers:** a faint "SI-assisted" mark in the bottom-right corner of the page, `si-disclosure` metadata in the
  HTML head, and a note in the API description (`/docs`).
- No images, video or audio were generated with SI, and no NASA branding is used.

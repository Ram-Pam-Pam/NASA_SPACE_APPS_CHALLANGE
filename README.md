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

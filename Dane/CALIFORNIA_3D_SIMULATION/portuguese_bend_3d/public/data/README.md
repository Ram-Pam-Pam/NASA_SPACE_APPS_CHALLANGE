# Dane dla frontendu: osuwisko Portuguese Bend (NISAR)

Pliki w tym folderze to gotowe elementy strony dla obszaru Portuguese Bend (Palos Verdes, Kalifornia).
Generuje je `scripts/export_web.py`. Wzorcowe użycie wszystkich plików jest w `../index.html`
(Leaflet 1.9.4 + Chart.js 4.4.1).

## Najważniejsze konwencje

- **Okres analizy (cały projekt): 23.11.2025 – 22.05.2026**, 14 dat co 12 dni, 13 kolejnych par, bez przerw
  i bez interpolacji (`meta.json → period`, `meta.json → dates`; `gap_after` jest puste). Dalszych danych nie
  używamy, bo scena z 3.06.2026 nie dała się dopasować (koherencja 0,07 w całej ramce).
- **Znak przemieszczenia:** wartość dodatnia oznacza ruch **w stronę satelity**, ujemna **od satelity**.
  Wszystkie wartości są mierzone wzdłuż linii widzenia radaru (LOS). Satelita jest na orbicie zstępującej
  i patrzy z zachodu pod kątem około 40°.
- **Jednostki:** przemieszczenie w mm, prędkość w mm/rok (tempo z 6 miesięcy przeliczone na rok; zawiera
  składową sezonową pory deszczowej), ruch zróżnicowany w mm na 100 m na rok.
- **Wektory:** GeoJSON w EPSG:4326 (lon/lat), współrzędne zaokrąglone do 5 miejsc po przecinku (ok. 1 m);
  budynki i punkty zagrożeń do 6 miejsc.
- **Rastry:** PNG w EPSG:3857 (Web Mercator), gotowe do nałożenia jako obraz:
  - Leaflet: `L.imageOverlay(url, meta.overlay_bounds)`, gdzie `overlay_bounds = [[S, W], [N, E]]`;
  - MapLibre / Mapbox: źródło `image` z narożnikami `[[W, N], [E, N], [E, S], [W, S]]` wziętymi z tych samych granic;
  - piksele przezroczyste oznaczają brak danych (woda, utrata spójności).

## Pliki

| Plik | Co zawiera |
|---|---|
| `meta.json` | okres i daty, granice nakładek, statystyki do nagłówka (rdzeń osuwiska, budynki, rurociągi, zagrożone elementy), kolory klas, liczby obiektów |
| **`threatened.geojson`** | **wszystkie zagrożone elementy (klasa wysokie lub średnie) w jednej warstwie: budynki + infrastruktura, jeden punkt na element** (znaczniki, lista, filtr) |
| `threatened.csv` | to samo jako tabela z `lon`, `lat` |
| `summary_threatened.json` | zestawienie zagrożonych: budynki wg klasy i typu, km infrastruktury wg typu i klasy, liczba potwierdzonych w monitoringu |
| **`buildings_risk.geojson`** | obrysy budynków: zagrożone (wysokie, średnie) w całym obszarze + wszystkie budynki do 500 m od aktywnej strefy (kontekst) |
| `summary_buildings.json` | budynki: liczba w obszarze, klasy, odległość od strefy aktywnej, typy, nazwane zagrożone budynki, progi |
| `rail.geojson` | kolej: czynne i nieczynne tory z klasą ryzyka |
| `power.geojson` | linie energetyczne z napięciem (`napiecie_kv`) i klasą ryzyka |
| `pipelines.geojson` | gazociągi i rurociągi (OGIM + OSM) z klasą ryzyka |
| `critical.geojson` | wszystko z trzech powyższych razem (bez torów nieczynnych) |
| `roads_risk.geojson` | drogi o średnim i wysokim ryzyku |
| `roads_context.geojson` | drogi główne jako tło |
| `active_zone.geojson` | obrys aktywnej strefy osuwiska (ruch szybszy niż 100 mm/rok), pole `pow_km2` |
| `alerts.geojson` | alerty wykrywania zmian: `typ` (przyspieszenie / nowa strefa ruchu / spowolnienie), `okno`, `data`, `pow_km2`, `v_mm_rok` |
| `profiles.geojson` + `profiles.json` | linie przekrojów i wartości wzdłuż nich dla każdej daty |
| `monitoring.json` | oś czasu wykrywania zmian (jedno okno = 4 kolejne pary, ok. 48 dni; 10 okien) |
| `summary_rail.json`, `summary_power.json` | podsumowania: km według klasy ryzyka, pokrycie danymi, podział według przeznaczenia / napięcia / operatora, lista obiektów |
| `timeseries.json` | przemieszczenie w każdym pikselu obszaru półwyspu dla każdej daty (wykres po kliknięciu) |
| `img/cum_00..13.png` | przemieszczenie od pierwszej daty, jedna klatka na datę (animacja) |
| `img/fringe_00..13.png` | to samo jako prążki interferometryczne (cykl 121 mm) |
| `img/velocity.png` | prędkość z całego okresu, tylko piksele z pełnym szeregiem (pewność pełna) |
| `img/velocity_all.png` | prędkość z uzupełnieniem z części par (większe pokrycie) |
| `img/confidence.png` | pewność danych: zielony = pełny szereg, pomarańczowy = część par |
| `img/coherence.png` | średnia koherencja (jakość sygnału) |
| `img/relief.png` | cieniowany relief jako podkład (Copernicus DEM), granice w `meta.relief_bounds` |

## Grafiki gotowe do wstawienia (`figures/`, wersje `_pl` i `_en`)

| Plik | Co pokazuje |
|---|---|
| `interferogram_pl/en.png` | prążki interferometryczne z białymi konturami ruchu co 100 mm, w stylu ilustracji NASA o leju Bayou Corne (NISAR Applications: Sinkholes); 1 cykl barw = 121 mm |
| `interferogram_etapy_pl/en.png` | ta sama mapa po 60, 120 i 180 dniach: jak narastał ruch |
| `interferogram_animacja_pl/en.gif` | animacja 14 dat (prążki i kontury narastają w czasie) |
| `warstwy3d_pl/en.png` | trzy warstwy 3D w stylu rysunku 2-5 z NISAR Science Users' Handbook: mapa deformacji, cieniowany widok deformacji (przewyższenie podane na grafice), zniekształcona mapa ulic |

Grafiki mają podpisy z wyjaśnieniem. Pole przemieszczenia jest w nich wygładzone do prezentacji (piksel danych 80 m),
zero odniesione do stabilnego terenu w kadrze; w widoku 3D luki w danych wypełniono interpolacją.

## Klasy zagrożenia (wspólne dla budynków i infrastruktury)

| Klasa | Kiedy |
|---|---|
| `wysokie` | grunt w zwartej strefie szybkiego ruchu (≥ próg `v_high`), albo silnie zróżnicowany ruch (≥ `g_high`) w strefie ruchu, albo alert w ostatnim oknie monitoringu; dla rur i torów także przecięcie granicy strefy ruchu |
| `średnie` | ruch ≥ `v_med` albo ruch zróżnicowany ≥ `g_med`, w zwartej strefie choćby umiarkowanego ruchu |
| `niskie` | pozostałe (ruch w granicach szumu pomiaru) |
| `brak danych` | za mało wiarygodnych pikseli (woda, utrata spójności, np. terminale portowe) |

Progi zależą od typu (rury i tory ostrzej niż drogi i budynki); ich dolna granica to 3σ (średnie) i 4σ (wysokie)
szumu tła prędkości (15,5 mm/rok w tym okresie). Wartości progów są w `summary_buildings.json → progi`.
Klasa budynku to **narażenie** (ruch gruntu pod budynkiem), nie ocena stanu konstrukcji.

Kolory klas (też w `meta.risk_colors`): wysokie `#c62a22`, średnie `#e3871f`, niskie `#2f8f57`,
brak danych `#98a3ab`.

## Pola `threatened.geojson` / `threatened.csv`

| Pole | Znaczenie |
|---|---|
| `kategoria` | `budynek` albo `infrastruktura` |
| `typ` | typ budynku z OSM (`house`, `apartments`, `industrial`, ...; `budynek` = bez typu) albo typ infrastruktury (`gazociąg`, `kolej`, `droga główna`, ...) |
| `nazwa`, `obiekt_id` | nazwa (jeśli jest) i identyfikator: `osm_<id>` dla budynków, jak w warstwach infrastruktury dla linii |
| `klasa`, `powod` | `wysokie` / `średnie` i uzasadnienie |
| `v` | prędkość gruntu [mm/rok] (ze znakiem) |
| `gradient` | ruch zróżnicowany [mm/100 m/rok] |
| `dlugosc_m` | długość odcinka w tej klasie (infrastruktura) |
| `pow_m2` | powierzchnia obrysu (budynki) |
| `odl_m` | odległość od aktywnej strefy osuwiska [m]; 0 = w strefie |
| `okna_ruch` | w ilu z 10 okien monitoringu grunt był w ruchu; **≥ 2 = ruch potwierdzony w czasie** |
| `pewnosc` | `pełna` (pełny szereg) albo `częściowa` (co najmniej 70% par) |

Lista jest posortowana: najpierw `wysokie`, potem `średnie`, w każdej klasie od najbliższych strefy aktywnej.
Pełną geometrię elementu znajdziesz po `obiekt_id` w `buildings_risk.geojson` lub w warstwach infrastruktury.

## Pola `buildings_risk.geojson`

| Pole | Znaczenie |
|---|---|
| `osm_id`, `nazwa`, `typ_budynku`, `pow_m2` | dane budynku z OSM |
| `klasa`, `powod` | narażenie i uzasadnienie (jak wyżej) |
| `v` | prędkość gruntu: największa (co do modułu) z 5 punktów obrysu [mm/rok] |
| `przem_mm` | przemieszczenie gruntu za cały okres w środku obrysu [mm] |
| `gradient`, `odl_m`, `okna_ruch`, `pewnosc` | jak w `threatened.geojson` |

Proponowany styl: wypełnienie kolorem klasy dla `wysokie` i `średnie`, budynki `niskie` (kontekst wokół strefy)
półprzezroczyste szare. Przy oddaleniu mapy lepiej pokazać `threatened.geojson` jako kropki.

## Pola warstw infrastruktury (`rail`, `power`, `pipelines`, `critical`, `roads_risk`)

Każdy obiekt to fragment jednej linii (`obiekt_id`) w jednej klasie ryzyka. Odcinki 100 m o tej samej klasie
są połączone.

| Pole | Znaczenie |
|---|---|
| `typ` | `gazociąg`, `rurociąg`, `ropociąg`, `kolej`, `kolej nieczynna`, `linia energetyczna`, `droga główna`, `droga lokalna` |
| `podtyp` | wartość źródłowa, np. `rail`, `light_rail`, `disused` (kolej); `line`, `cable`, `line (busbar)` (energetyka) |
| `nazwa`, `operator` | nazwa linii i operator, jeśli są w źródle |
| `napiecie_kv` | napięcie w kV jako tekst, np. `220`, `138`, `66;12` (tylko energetyka) |
| `uzycie` | przeznaczenie toru, np. `main`, `branch`, `industrial / yard` |
| `obiekt_inz` | `most`, `tunel`, `pod ziemią` albo puste |
| `klasa` | **ryzyko:** `wysokie`, `średnie`, `niskie`, `brak danych` |
| `powod` | uzasadnienie klasy, np. „gradient 31 mm/100m/rok (próg 30); przecina granicę strefy ruchu” |
| `pewnosc` | `pełna` (pełny szereg pomiarów) albo `częściowa` (co najmniej 70% par) |
| `v` | prędkość [mm/rok], 95. percentyl w korytarzu wokół linii (ze znakiem) |
| `gradient` | maks. ruch zróżnicowany [mm/100 m/rok] |
| `km` | długość w tej klasie [km] |
| `odl_m` | odległość obiektu od aktywnej strefy osuwiska [m] |
| `schem` | `true` = przebieg schematyczny (oceniany w korytarzu ±300 m) |
| `zrodlo`, `obiekt_id` | plik źródłowy i identyfikator (np. `way/123` z OSM, `OGIM_...`) |

Proponowany styl typów: rurociąg linią ciągłą i grubszą, kolej linią przerywaną, energetyka kropkowaną;
nieczynne tory na szaro.

## Skale kolorów rastrów

- Przemieszczenie i prędkość: rozbieżna skala RdBu, od −150 (ciemnoczerwony, od satelity) przez 0 (biały)
  do +150 (ciemnoniebieski, do satelity). Wartość graniczna jest w `meta.scale_mm`; wartości poza zakresem
  są nasycone.
- Prążki: cykliczna skala HSV; jeden pełny cykl = `meta.half_wavelength_mm` (121 mm); zero to środek cyklu.

## `timeseries.json`: wartość w klikniętym punkcie

```js
const ts = await (await fetch("data/timeseries.json")).json();      // {shape:[h,w,E], b64, nodata:-32768}
const bin = atob(ts.b64), u8 = new Uint8Array(bin.length);
for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
const TS = new Int16Array(u8.buffer);                                // Int16 little-endian, mm
const F = meta.focus;                                               // siatka EPSG:3857: x0, y1, res, w, h
const p = L.CRS.EPSG3857.project(latlng);                            // metry Web Mercator
const col = Math.floor((p.x - F.x0) / F.res), row = Math.floor((F.y1 - p.y) / F.res);
const series = [...Array(F.epochs).keys()].map(e => TS[(row * F.w + col) * F.epochs + e]);  // -32768 = brak
```

## `profiles.json`

Lista przekrojów: `name`, `distance_m` (odległość wzdłuż linii), `values_mm[data][punkt]` (null = brak danych)
oraz `crossings` (przecięcia z infrastrukturą: `odleglosc_m`, `klasa`, `obiekt`).

## Atrybucja (wymagana na stronie)

NISAR L2 GUNW (NASA/ISRO, ASF DAAC) · Copernicus DEM GLO-30 (ESA/UE) · © OpenStreetMap contributors (ODbL;
drogi, kolej, budynki) · baza OGIM (gazociągi).

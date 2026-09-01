# APDS — Szablon mapowania pól XET

## 1. Dokument

**Projekt:** ALIAS Product Data Synchronization (APDS)
**Moduł:** `hfb_apds`
**Platforma docelowa:** Odoo 19 CE

Dokument definiuje **szablon** (strukturę) wykorzystywany do prowadzenia mapowania pól:

```text
pole źródłowe JSON  →  mapowanie XET  →  model/pole Odoo
```

Jest to narzędzie robocze — jednolity format zapisu dla każdego pola źródłowego, niezależnie od tego, na jakim etapie decyzyjnym się ono znajduje. Ma zastąpić rozproszone notatki (arkusz mapowania, arkusz komentarzy ze spotkania) jedną, spójną strukturą, którą można rozwijać wiersz po wierszu.

Zgodnie z zasadą projektową z `APDS_projekt.md` (sekcja 10), ten szablon dokumentuje **decyzje mapowania na poziomie procesu biznesowego** — nie jest jeszcze specyfikacją implementacyjną modułu `hfb_apds` ani konfiguracją techniczną XET Base.

---

## 2. Struktura wiersza mapowania

Każde pole źródłowe opisywane jest przez następujący zestaw atrybutów:

| Kolumna | Opis |
|---|---|
| **ID** | Krótki identyfikator pola, np. `F-001`. Ułatwia odwołania w dyskusji/decyzjach. |
| **Ścieżka JSON** | Pełna ścieżka do pola źródłowego, np. `price.sell.netto`, `flags.urgent`. |
| **Typ źródłowy** | Typ obserwowany w danych: tekst / liczba / boolean / null / tablica / obiekt. |
| **Model Odoo** | Docelowy model, np. `product.template`, `product.supplierinfo`, `ksef.gtu`. Jeśli nieustalony: `?`. |
| **Pole Odoo** | Docelowe pole istniejące (`default_code`) lub `NOWE POLE` z proponowanym typem. |
| **Typ mapowania** | Jedna z wartości: `bezpośrednie` / `transformacja` / `logika biznesowa` / `nowe pole` / `do ustalenia`. |
| **Reguła / transformacja** | Opis sposobu przeliczenia lub złożenia wartości, np. `prefix + " " + index`. |
| **Status decyzji** | `ustalone` / `robocze` / `otwarte` (patrz legenda niżej). |
| **Powiązanie** | Odniesienie do `APDS_projekt.md` (np. `UC-06`, `9.5`) lub do `ALIAS_Struktura_danych_zrodlowych.md`. |
| **Komentarz** | Uwagi, warunki brzegowe, pytania otwarte. |

### Legenda statusów

| Status | Znaczenie |
|---|---|
| **ustalone** | Reguła mapowania jest potwierdzona i może być podstawą dalszego projektowania/implementacji. |
| **robocze** | Kierunek jest znany, ale szczegóły (typ pola, próg, reguła graniczna) wymagają dopracowania. |
| **otwarte** | Brak decyzji — pole wymaga jeszcze ustalenia z klientem lub w zespole. |

---

## 3. Przykładowe wypełnienie szablonu

Poniżej — wypełnienie na bazie dotychczasowych ustaleń (arkusz mapowania pól oraz notatki ze spotkania 28.08.26). To ilustracja formatu, nie ostateczna wersja mapowania — kolejne pola należy uzupełniać w tym samym układzie.

### 3.1. Pola podstawowe

| ID | Ścieżka JSON | Typ źródłowy | Model Odoo | Pole Odoo | Typ mapowania | Reguła / transformacja | Status | Powiązanie | Komentarz |
|---|---|---|---|---|---|---|---|---|---|
| F-001 | `prefix` + `index` | tekst + tekst | `product.template` | `default_code` | transformacja | `prefix + " " + index` | ustalone | UC-06, 5 (struktura) | Klucz identyfikacyjny produktu |
| F-002 | `name` | tekst | `product.template` | `name` | bezpośrednie | — | ustalone | — | — |
| F-003 | `description` | tekst | — | — | do ustalenia | — | otwarte | — | Wg komentarza: „czerwone pomijamy” — znaczenie tego zapisu wymaga wyjaśnienia |
| F-004 | `info` | tekst | — | — | do ustalenia | — | otwarte | 14 (struktura) | Brak ustalonego przeznaczenia |
| F-005 | `producer` | tekst | `product.template` | NOWE POLE (char) | nowe pole | — | robocze | 9.6 | — |
| F-006 | `ean` | tekst | `product.template` | `barcode` | logika biznesowa | Jeśli duplikat EAN → log + pominięcie utworzenia rekordu, zebranie listy wyjątków | robocze | UC-07 | Reguła obsługi duplikatu do sformalizowania jako UC |
| F-007 | `uom` | tekst | `product.template` | `uom_id` | transformacja | Mapowanie tekstu na `uom.uom` | robocze | — | Sposób dopasowania/tworzenia jednostki nieustalony |
| F-008 | `pkwiu` | tekst | `product.template` | NOWE POLE (char) | nowe pole | — | robocze | 9.6 | — |
| F-009 | `cn` | tekst | `product.template` | NOWE POLE (char/many2one) | do ustalenia | — | otwarte | 9.6 | Pole kodu CN/HS dostępne natywnie tylko w Enterprise (`account_intrastat`) |
| F-010 | `gtu` | null/tekst | `ksef.gtu` | NOWE POLE (many2one) | nowe pole | Powiązanie z istniejącym modelem `ksef.gtu` | robocze | 9.6 | — |
| F-011 | `tax` | tekst | `product.template` | `taxes_id` | transformacja | Domyślnie `23%`; pole `taxes_id` jest many2many | robocze | — | Reguła doboru stawki przy innych wartościach nieustalona |

### 3.2. Obiekt `price`

| ID | Ścieżka JSON | Typ źródłowy | Model Odoo | Pole Odoo | Typ mapowania | Reguła / transformacja | Status | Powiązanie | Komentarz |
|---|---|---|---|---|---|---|---|---|---|
| F-012 | `price.buy.netto` | liczba | `product.supplierinfo` | `price` / `standard_price` | bezpośrednie | Cena zakupu netto | robocze | 6 (struktura) | Docelowy model do potwierdzenia (supplierinfo vs standard_price) |
| F-013 | `price.buy.brutto` | liczba | — | — | do ustalenia | — | otwarte | — | — |
| F-014 | `price.sell.netto` | liczba | `product.template` | `list_price` | bezpośrednie | Odoo liczy domyślnie w cenach netto | ustalone | — | — |
| F-015 | `price.sell.brutto` | liczba | — | — | do ustalenia | — | otwarte | — | Wartość pochodna — czy przechowywana czy wyliczana |
| F-016 | `price.srp` | liczba | — | — | do ustalenia | — | otwarte | 14 (struktura) | — |
| F-017 | `price.profit.netto` / `.brutto` | liczba | — | — | do ustalenia | — | otwarte | 14 (struktura) | — |

### 3.3. Obiekt `stock`

| ID | Ścieżka JSON | Typ źródłowy | Model Odoo | Pole Odoo | Typ mapowania | Reguła / transformacja | Status | Powiązanie | Komentarz |
|---|---|---|---|---|---|---|---|---|---|
| F-018 | `stock.local` | liczba | `stock.quant` | `qty_available` (?) | logika biznesowa | Tylko import początkowy; dalsze zmiany stanu poza JSON | robocze | 7 (struktura) | Sposób docelowej aktualizacji do ustalenia |
| F-019 | `stock.foreign` | liczba | — | — | do ustalenia | — | otwarte | — | Nie występuje w obserwowanych danych |
| F-020 | `stock.supplier` | liczba | `product.template` | NOWE POLE (float) | nowe pole | — | robocze | — | — |
| F-021 | `stock.total` | liczba | `product.template` | NOWE POLE (float) | logika biznesowa | `stock.total == 0` → rekord pomijany (eliminacja części duplikatów) | **ustalone** | UC-05, 7 (struktura) | Reguła filtrująca na wejściu do przetwarzania |

### 3.4. Dostawca, kategoria, rabaty

| ID | Ścieżka JSON | Typ źródłowy | Model Odoo | Pole Odoo | Typ mapowania | Reguła / transformacja | Status | Powiązanie | Komentarz |
|---|---|---|---|---|---|---|---|---|---|
| F-022 | `supplier` | tekst | `product.supplierinfo` | `partner_id.name` | transformacja | — | robocze | — | — |
| F-023 | `supplier_id` | tekst | `product.supplierinfo` | — | transformacja | — | robocze | — | — |
| F-024 | `oe` | tekst | — | — | do ustalenia | — | otwarte | 14 (struktura) | — |
| F-025 | `discount_group` | tekst | `product.template` | NOWE POLE (char/many2one) | do ustalenia | — | otwarte | 9.6 | — |
| F-026 | `category.id` / `category.name` | liczba / tekst | `product.category` | `categ_id` | logika biznesowa | Tworzenie kategorii jeśli nie istnieje | ustalone | — | — |

### 3.5. Tablice i flagi

| ID | Ścieżka JSON | Typ źródłowy | Model Odoo | Pole Odoo | Typ mapowania | Reguła / transformacja | Status | Powiązanie | Komentarz |
|---|---|---|---|---|---|---|---|---|---|
| F-027 | `images[]` | tablica | `product.template` | `image_1920` | transformacja | Sposób obsługi wielu obrazów nieustalony | robocze | 11 (struktura) | — |
| F-028 | `flags.*` (12 pól) | boolean | `product.template` | tagi produktu | logika biznesowa | `true` → zastosowanie tagu o nazwie flagi | ustalone (kierunek) | 9 (struktura) | Nazewnictwo tagów do ustalenia |
| F-029 | `compatibility[]` | tablica | — | — | do ustalenia | — | otwarte | 11 (struktura) | Brak przykładowych danych ze strukturą elementu |
| F-030 | `attributes[]` | tablica | — | — | do ustalenia | — | otwarte | 11 (struktura) | Struktura elementu nieznana |
| F-031 | `conn.repl[]` | tablica tekstów | — | — | do ustalenia | — | otwarte | 10 (struktura) | Znaczenie i wykorzystanie nieustalone |
| F-032 | `conn.link[]` | tablica tekstów | — | — | do ustalenia | — | otwarte | 10 (struktura) | — |

---

## 4. Zasady prowadzenia mapowania

1. **Jeden wiersz = jedno pole źródłowe** (lub jasno wydzielona grupa pól o wspólnej regule, np. `flags.*`).
2. **Status zmienia się tylko w jedną stronę w danym momencie** — z `otwarte` → `robocze` → `ustalone`. Cofnięcie statusu wymaga odnotowania powodu (np. w kolumnie Komentarz).
3. **Powiązanie z `APDS_projekt.md`** — jeśli decyzja dotyczy reguły procesowej (np. pomijanie rekordu, obsługa duplikatu), wiersz mapowania powinien wskazywać właściwy UC lub sekcję, a nie tylko opisywać regułę lokalnie.
4. **Rozróżnienie pól strukturalnych i biznesowych** — zgodnie z `ALIAS_Struktura_danych_zrodlowych.md` (sekcja 15), ten szablon nie zastępuje dokumentu struktury danych źródłowych; opisuje wyłącznie *docelowe mapowanie*, nie samą strukturę JSON.
5. **Puste wartości nie są automatycznie równoważne** — mapowanie pola z `null`, pustym tekstem lub `0` powinno jawnie wskazywać, czy te przypadki są traktowane tożsamo, czy różnicowane (zgodnie z sekcją 12 dokumentu struktury danych).

---

## 5. Status dokumentu

**Status:** szablon roboczy — pierwsza wersja

Tabele w sekcji 3 stanowią wypełnienie przykładowe na bazie dotychczasowych ustaleń i wymagają:
- uzupełnienia brakujących wierszy (pola oznaczone jako `otwarte`),
- potwierdzenia modeli/pól Odoo tam, gdzie oznaczono `?` lub `do ustalenia`,
- powiązania każdego wiersza z odpowiednim UC w `APDS_projekt.md`, tam gdzie dotyczy to reguły procesowej, a nie tylko technicznego przypisania pola.

Dokument powinien być aktualizowany równolegle z `APDS_projekt.md` — bez usuwania wcześniejszych ustaleń, jedynie z odnotowywaniem zmian statusu.

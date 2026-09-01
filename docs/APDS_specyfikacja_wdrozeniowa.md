# ALIAS Product Data Synchronization (APDS) — Specyfikacja wdrożeniowa (szkic pod prototyp)

## Status dokumentu

**Status:** roboczy szkic techniczno-funkcjonalny, przygotowywany pod prototyp dla klienta.

Ten dokument jest **osobny od `APDS_projekt.md`** (dokumentu koncepcyjnego procesu biznesowego). Zgodnie z zasadą projektową ustaloną w `APDS_projekt.md` (sekcja 10): najpierw proces biznesowy, potem nakładka na XET Base, dopiero potem implementacja. Ten dokument wchodzi już w warstwę implementacji — powstaje wcześniej niż wszystkie decyzje biznesowe zostały zamknięte, świadomie, ponieważ jego celem jest **prototyp do rozmowy z klientem**, a nie gotowa specyfikacja do ślepego zaprogramowania.

**Zasada tego dokumentu:** tam, gdzie decyzja biznesowa nie została jeszcze podjęta (patrz `APDS_projekt.md`, sekcja 9), ten dokument podaje **założenie robocze** — wystarczające, żeby prototyp działał i można było na nim pokazać klientowi konkretne zachowanie, ale wyraźnie oznaczone jako **do potwierdzenia**, nie jako ustalony fakt. Założenia robocze powinny być łatwe do zmiany (konfiguracja, nie twardy kod), bo część z nich się zmieni po rozmowie z klientem.

Odniesienia do `APDS_projekt.md` w nawiasach kwadratowych, np. [9.4], wskazują odpowiednią sekcję tamtego dokumentu.

---

## 1. Architektura techniczna stanu 2 i 3 (wydajność i transakcyjność)

Skala ~2,12 mln rekordów wyklucza standardowe przetwarzanie rekord-po-rekordzie przez ORM Odoo [4.6].

### 1.1. Tabela tymczasowa (staging) [ZMIANA względem wcześniejszej wersji]

**Ta sekcja zastępuje wcześniejszą treść** (tabela z kolumną `raw_data jsonb` i dwustanowym modelem `draft`/`processed`/`error`). Ustalenia zmieniły się w toku dalszej pracy — pozostawiam notatkę, żeby dokumenty się nie rozjeżdżały, zamiast utrzymywać dwa niezależne schematy tej samej tabeli w dwóch plikach.

**Jedyne autorytatywne źródło struktury `hfb_apds_staging_line` (pełny DDL, indeksy, uzasadnienia): `APDS_wczytanie_JSON_i_workery.md`, sekcje 4.1-4.4 i 4a.**

Skrótowo, co się zmieniło względem pierwotnej propozycji:
- **brak kolumny `raw_data jsonb`** — staging przechowuje rozbite pola (`default_code`, `name`, `price_sell_netto`, rozwiązane `categ_id`/`uom_id`/`tax_id` itd.), nie surowy JSON; mapowanie XET rozwiązywane raz, w Etapie 2,
- stan wiersza to **cztery** wartości (`draft`/`processing`/`processed`/`error`), nie trzy — `processing` niesie `worker_id`+`reserved_at`, potrzebne przy pracy wielu równoległych workerów (nieobecnych jeszcze w pierwotnej wersji tego dokumentu),
- rezerwacja przez `SELECT ... FOR UPDATE SKIP LOCKED`, nie przez samo `draft → processed`,
- brak rzeczywistych `REFERENCES` na `run_id`/`categ_id`/`uom_id`/`tax_id`/`odoo_product_id` — decyzja pod kątem wydajności,
- staging w całości kasowany po zamknięciu przebiegu (patrz 6.3) — nie jest trwałym repozytorium do przeglądania.

Kwestia idempotencji przy ponowieniu batcha (opisana tu pierwotnie w kontekście dwustanowego modelu) pozostaje otwarta — śledzona w `APDS_wczytanie_JSON_i_workery.md`, sekcja 13, punkt 11.

### 1.2. Rozmiar paczek (batching)

Commit do bazy Odoo co N rekordów (przykładowo: 1000–5000), zamiast jednej transakcji na 2,12 mln rekordów — unika przepełnienia pamięci i długotrwałych blokad tabel.

**Konkretna wartość N nie jest ustalona** — wymaga testów wydajnościowych na rzeczywistym wolumenie danych, nie jest to decyzja do podjęcia teraz.

---

## 2. Semantyka pliku różnicowego i cyklu życia produktu

### 2.1. Brak produktu w pliku pełnym [9.1]

**Decyzja biznesowa nierozstrzygnięta.** Opcje: archiwizacja (`active = False`), pominięcie, inna operacja.

**Założenie robocze do prototypu:** brak działania (produkt pozostaje w Odoo bez zmian). To najbezpieczniejsza opcja demonstracyjna — nic nieodwracalnego się nie dzieje, dopóki klient nie potwierdzi właściwej reguły.

### 2.2. Interpretacja rekordu w pliku różnicowym [9.2]

**Decyzja biznesowa nierozstrzygnięta**, w tym nowe pytanie: czy `stock.total == 0` w pliku różnicowym oznacza usunięcie produktu, czy tylko chwilowy brak towaru u dostawcy (wyprzedany zapas)?

**Założenie robocze do prototypu:** `stock.total == 0` traktowane jest **wyłącznie jako informacja o stanie magazynowym** (zgodnie z regułą pomijania rekordu z sekcji 6.3 `APDS_projekt.md`), nigdy jako sygnał usunięcia produktu. Usuwanie/archiwizacja produktów nie jest realizowane w prototypie w żadnym scenariuszu — to świadome uproszczenie, do jawnego zakomunikowania klientowi jako coś, czego prototyp *nie* pokazuje.

### 2.3. Konflikt modyfikacji — pole zmienione ręcznie w Odoo vs dane źródłowe

Zasada "JSON to master data" [4.1] mówi, że dane źródłowe nadpisują lokalne. Ale to, które pola faktycznie temu podlegają, ustalaliśmy częściowo w rozmowie o politykach pól. Na tej podstawie:

**Pola w zakresie prototypu i ich proponowana polityka:**

| Pole JSON | Cel Odoo | Polityka (robocza) |
|---|---|---|
| `prefix`+`index` | `default_code` | klucz dopasowania, nie podlega polityce nadpisywania |
| `name` | `name` | `source_owned` (robocze — **do potwierdzenia**, czy zespół sprzedaży chce lokalnie edytować nazwy) |
| `ean` | `barcode` | `source_owned` |
| `uom` | `uom_id` | `source_owned` (wymaga słownika mapowania — patrz 5.3) |
| `tax` | `taxes_id` | `source_owned`, wartość domyślna 23% |
| `price.buy.netto` | `standard_price` / dane dostawcy | `source_owned` |
| `price.sell.netto` | `list_price` | `source_owned` |
| `stock.local` | stan lokalny | tylko import początkowy — dalsze zasady aktualizacji nieustalone |
| `stock.total` | nowe pole | `source_owned`; `0` → pomiń rekord |
| `category.id`/`name` | `categ_id` | `source_owned`; tworzone jeśli nie istnieje |
| `flags.*` | tagi | reguła specjalna, nie prosta polityka pola (patrz `APDS_projekt.md` — obecność tagu zależy od wartości `true`/`false`, w tym pytanie o usuwanie tagu przy zmianie na `false` — **nierozstrzygnięte**) |

Pola oznaczone w arkuszu klienta na czerwono (`description`, `images`, `supplier`/`supplier_id`, `producer`, `pkwiu`, `cn`, `gtu`, `discount_group`, `stock.supplier`, `oe`, `compatibility`, `conn.*`, `attributes`, `info`, `price.srp`, `price.profit.*`, `stock.foreign`) **pozostają poza zakresem prototypu**. Nie mają jeszcze polityki aktualizacji, bo nie są jeszcze synchronizowane w ogóle.

---

## 3. Matryca klasyfikacji błędów

### 3.1. Błąd lokalny — pominięcie wiersza

Wiersz w tabeli tymczasowej oznaczany jako `state = error`, `error_message` wypełnione, log wyjątku zapisany, przetwarzanie kontynuowane od kolejnego wiersza.

Przykłady:
- niepoprawny format EAN,
- brak nazwy produktu,
- duplikat EAN (patrz `APDS_projekt.md` 6.3 — dodatkowo zbierany na osobnej liście wyjątków),
- `stock.total == 0` (to jest pominięcie z reguły biznesowej, nie błąd — technicznie może być realizowane tym samym mechanizmem `state`, ale semantycznie to nie jest "błąd").

### 3.2. Błąd krytyczny — przerwanie stanu 3

Cały stan 3 zatrzymywany, `apds_status = failed` (nazwa robocza pola, patrz `APDS_projekt.md` 2.1), powiadomienie do administratora przez `notification_users` (pole już istniejące na `communication.provider`).

Przykłady:
- awaria połączenia z bazą danych,
- brak miejsca na dysku,
- uszkodzenie/niedostępność samej tabeli tymczasowej.

**Nierozstrzygnięte (patrz `APDS_projekt.md` [9.3]):** pełna, wyczerpująca lista, które błędy kwalifikują się jako krytyczne vs lokalne — powyższe to tylko reprezentatywne przykłady, nie zamknięty katalog.

---

## 4. Algorytm kontroli biznesowej (wyzerowanie stanów) [9.4]

Wzór:

```
X = (liczba wierszy w tabeli tymczasowej z stock.total == 0) / (wszystkie wiersze w tabeli tymczasowej) × 100%
```

**Próg alarmowy: NIEUSTALONY.** Nie ma potwierdzonej wartości — żadna liczba nie padła dotąd ani na spotkaniu z klientem, ani w żadnych naszych ustaleniach. Wartość musi być **konfigurowalna** (nie zaszyta na stałe), żeby dało się ją skalibrować po rozmowie z klientem i obserwacji rzeczywistych danych. Do celów samego prototypu potrzebna jest jakaś wartość startowa — proponuję to ustalić świadomie, a nie przyjmować bez dyskusji.

**Zasięg kontroli — nieustalony:** wszystkie produkty łącznie, czy per dostawca (`supplier`)? To ma znaczenie praktyczne — jeśli jeden dostawca ma realny problem z zaopatrzeniem, kontrola "po całości" może tego nie wychwycić, a kontrola per dostawca mogłaby zgłosić fałszywy alarm dla małych dostawców z naturalnie zmienną dostępnością.

**Procedura po przekroczeniu progu:** stan procesu ustawiany na **Wymaga interwencji ręcznej** (`APDS_projekt.md` 6.1), przejście do stanu 3 wstrzymane, powiadomienie wysyłane do `notification_users`.

---

## 5. Specyfikacja mapowania pod Odoo Community Edition

### 5.1. Kod CN (commodity code / HS code)

Model `account.intrastat` nie istnieje w Odoo CE (dostępny tylko w Enterprise).

**Propozycja do prototypu:** proste pole `char` na `product.template`, bez własnego modelu — najmniejszy koszt wdrożenia. Osobny model (`apds.commodity.code` lub podobny) jako możliwa ścieżka rozwoju później, jeśli okaże się potrzebna dodatkowa logika (np. walidacja formatu, powiązanie z tabelą celną).

To pole jest obecnie **poza zakresem prototypu** (czerwone w arkuszu klienta — patrz 2.3) — ta sekcja to przygotowanie na przyszłość, nie element prototypu w pierwszej wersji.

### 5.2. Grupa rabatowa (`discount_group`)

Do decyzji: pole tekstowe, czy model `many2one`. Jeśli model — czy brakująca grupa tworzy się automatycznie (analogicznie do kategorii, patrz `APDS_projekt.md` sekcja 8), czy zgłasza błąd.

**Propozycja do prototypu:** pole tekstowe — najprostsze, odwracalne. Migracja do `many2one` później nie jest kosztowna, jeśli dane wejściowe to proste kody.

Również poza zakresem prototypu w pierwszej wersji (czerwone w arkuszu).

### 5.3. Słownik jednostek miary (`uom`)

Potrzebna jawna tabela mapowania stringów z JSON (np. `"szt."`) na istniejące `uom_id` w Odoo — nie można zakładać, że nazwy się pokrywają 1:1.

**Propozycja:** tabela konfiguracyjna (nie kod), edytowalna bez zmiany modułu — nowa, nieznana wcześniej wartość jednostki z pliku powinna dać się dodać do słownika bez wdrożenia.

---

## 6. Projekt UI i struktury audytowej (`communication.log`)

### 6.1. Podmenu APDS

Widok listy (tree view) przefiltrowany do przebiegów `communication.log` z providerem typu ALIAS — analogicznie do istniejących widoków dla KSeF.

### 6.2. Podsumowanie na koniec stanu 3 — źródło danych do raportu

Liczby stanowiące podstawę raportu końcowego (patrz 6.3):
- liczba przetworzonych rekordów,
- liczba utworzonych produktów,
- liczba zaktualizowanych produktów,
- liczba pominiętych (`stock.total == 0`),
- liczba błędów pojedynczych rekordów, w podziale na kategorię (`state = error` w tabeli tymczasowej).

### 6.3. Raport końcowy — PDF, nie trwały widok wyjątków [ZMIANA względem wcześniejszej wersji]

**Ta sekcja zastępuje wcześniejszą treść** ("Widok wyjątków — logi/rekordy powiązane", wzorzec `parent_id`/`child_ids`). Decyzja zmieniona w `APDS_wczytanie_JSON_i_workery.md` (sekcja 6b) po analizie kosztu przy dużej skali błędów — pozostawiam tu notatkę, żeby dwa dokumenty się nie rozjeżdżały.

**Ustalone:** żadnego trwałego, przeglądanego widoku powiązanych rekordów błędów. Zamiast tego — **raport PDF generowany raz, na koniec stanu 3**, zawierający wyłącznie:
- liczby zbiorcze per kategoria błędu (duplikat EAN, duplikat `default_code`, brak nazwy...),
- ograniczoną (bounded) próbkę przykładów na kategorię (np. 20-50 wierszy), do zdiagnozowania charakteru problemu.

**Nigdy pełnej listy** — przy dużej skali (tysiące/dziesiątki tysięcy błędów) generowanie i przeglądanie pełnej listy jest kosztowne i nieproporcjonalne do wartości; duży wolumen błędów jednego typu to sygnał problemu systemowego w źródle, widoczny już w samych liczbach zbiorczych.

**Format: PDF**, wg istniejącego wzorca QWeb już użytego w repozytorium `hfb_xmlmap_exporter` dla raportu UPO (`reports/invoice_report.py` + `views/upo_pdf_templates.xml`) — kontynuacja istniejącej konwencji. XLSX rozważany i odrzucony: skoro nie generujemy pełnej listy, nie ma czego wkładać do arkusza ponad to, co mieści się w zwięzłym PDF; nowa zależność (`openpyxl`/`xlsxwriter`) nieuzasadniona.

Zapisywany jako `ir.attachment` na rekordzie `communication.log`.

**Konsekwencja:** tabela tymczasowa (`hfb_apds_staging_line`) jest w całości kasowana po wygenerowaniu raportu — pełna lista błędów nie jest dostępna po zamknięciu przebiegu w żadnej formie. Świadomie zaakceptowane.

---

## 7. Co ten dokument świadomie pomija

Zgodnie z zasadą projektową — poniższe pozostaje przedmiotem decyzji biznesowych w `APDS_projekt.md`, nie jest tu rozstrzygane:
- ostateczna wartość progu zerowania stanów (sekcja 4),
- ostateczna reguła dla produktu znikającego z pliku pełnego (2.1),
- pełna semantyka pliku różnicowego (2.2),
- pełna klasyfikacja błędów lokalny/krytyczny (3.2),
- polityka pola `name` i reguła znikania tagów z `flags.*` (2.3).

Prototyp budowany na bazie tego dokumentu powinien **jawnie sygnalizować** klientowi, które zachowania są tylko założeniami roboczymi do potwierdzenia, a nie gotowymi decyzjami.

---

## 8. Status i dalsze kroki

**Status:** szkic wdrożeniowy, niezweryfikowany z klientem, przygotowany pod prototyp.

Nie zastępuje `APDS_projekt.md` — jest jego rozwinięciem technicznym w miejscach, gdzie potrzebna była konkretyzacja pod cele prototypu.

# APDS — Wczytanie JSON i przetwarzanie z użyciem workerów

## 1. Cel

Dokument zapisuje dotychczasowe ustalenia dotyczące technicznej realizacji wczytania dużego pliku JSON do bazy danych oraz późniejszego, równoległego przetwarzania danych w Odoo.

Dokument dotyczy wyłącznie tego obszaru. Szczegółowa architektura całego APDS będzie rozwijana w osobnym `APDS_projekt_techniczny.md`.

## 2. Założenia wejściowe

Źródłem danych jest plik JSON dostarczany przez klienta.

Struktura źródłowa ma postać jednej dużej tablicy obiektów. Skala pełnego pliku wynosi około **2,12 mln rekordów**. Plik jest dostarczany w archiwum 7z.

## 3. Wczytanie JSON

Ze względu na skalę nie zakładamy:

```text
JSON → Odoo ORM create()
```

dla całego pliku ani wczytania całej tablicy JSON do pamięci procesu Odoo.

Ustalony kierunek:

```text
plik 7z
   ↓
rozpakowanie strumieniowe
   ↓
streaming JSON parser
   ↓
porcje rekordów
   ↓
PostgreSQL COPY FROM STDIN
   ↓
tabela staging
```

PostgreSQL `COPY` nie służy do bezpośredniego importu pliku będącego jedną dużą tablicą JSON. JSON musi zostać odczytany strumieniowo i przekazany do `COPY` w odpowiedniej postaci.

Preferowany kierunek to strumień rekordów bez tworzenia dodatkowego pliku pośredniego:

```text
JSON parser
   ↓
strumień rekordów
   ↓
COPY FROM STDIN
```

## 4. Tabela staging

Dane mają trafić do pomocniczej tabeli PostgreSQL.

Robocza nazwa:

```text
hfb_apds_staging_line
```

Staging jest obszarem pomiędzy przygotowaniem danych a właściwym przetwarzaniem.

Ma umożliwiać:

- porcjowanie,
- równoległe przetwarzanie,
- śledzenie stanu rekordu,
- wznowienie po awarii,
- ponowienie nieudanego przetwarzania.

**Ustalone (patrz 12 — moment rozwiązywania mapowania):** staging przechowuje **rozbite pola**, nie surowy `jsonb`. Mapowanie XET (`src_rel_path`/`value_expr`) jest rozwiązywane raz, w Etapie 2, podczas strumieniowego przetwarzania pliku — nie ponownie przy każdym odczycie w Etapie 3.

### 4.1. Struktura tabeli — ustalona

```sql
CREATE TABLE hfb_apds_staging_line (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              INTEGER NOT NULL,          -- bez REFERENCES, patrz 4.2

    -- klucz identyfikacyjny (prefix + " " + index)
    default_code        TEXT,               -- nullable: brak = błąd rekordu, nie błąd całego COPY

    -- pola źródłowe w zakresie (sekcja 8 APDS_projekt.md, tylko "zielone")
    name                TEXT,
    ean                 TEXT,                -- -> barcode
    uom_raw             TEXT,                -- surowa wartość z JSON, np. "szt."
    tax_raw             TEXT,                -- np. "23%"
    price_buy_netto     NUMERIC,             -- bez wymuszonej skali, patrz 4.3
    price_sell_netto    NUMERIC,
    stock_local         NUMERIC,
    stock_total         NUMERIC,             -- reguła: 0 -> pomiń rekord
    category_id_src     INTEGER,             -- id kategorii z JSON
    category_name       TEXT,

    -- flagi -> tagi (sekcja 9 ALIAS_Struktura_danych_zrodlowych.md)
    flag_urgent BOOLEAN, flag_watch BOOLEAN, flag_quality BOOLEAN, flag_new BOOLEAN,
    flag_recom BOOLEAN, flag_promo BOOLEAN, flag_sale BOOLEAN, flag_used BOOLEAN,
    flag_serviced BOOLEAN, flag_open BOOLEAN, flag_use BOOLEAN, flag_allegro_product BOOLEAN,

    -- rozwiązane FK do Odoo, uzupełniane po pre-passie (Etap 2, po COPY)
    categ_id            INTEGER,             -- bez REFERENCES, patrz 4.2
    uom_id               INTEGER,
    tax_id                INTEGER,

    -- stan przetwarzania (sekcja 9)
    state                TEXT NOT NULL DEFAULT 'draft'
                         CHECK (state IN ('draft','processing','processed','error')),
    worker_id            TEXT,
    reserved_at          TIMESTAMPTZ,
    error_message        TEXT,
    odoo_product_id      INTEGER,             -- bez REFERENCES

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.2. Decyzja: bez rzeczywistych `REFERENCES` na `run_id`/`categ_id`/`uom_id`/`tax_id`/`odoo_product_id`

**Ustalone pod kątem szybkości działania.** PostgreSQL sprawdza więzy FK przy każdym wstawieniu/aktualizacji — dla `run_id` na każdym z 2,12 mln wierszy podczas `COPY` (najgorętsza ścieżka procesu), dla `categ_id`/`uom_id`/`tax_id` na każdym wierszu podczas zbiorczego `UPDATE ... JOIN` w pre-passie (jednowątkowe tworzenie brakujących encji referencyjnych przed Etapem 3).

Dodatkowy argument, nie tylko wydajnościowy: te kolumny są wypełniane przez własny kod (`UPDATE ... JOIN` z tabel referencyjnych Odoo), nie pochodzą bezpośrednio z niezaufanego JSON-a klienta — FK chroniłby przed błędem, którego ta droga zapisu strukturalnie nie może popełnić.

Świadomy koszt: brak siatki bezpieczeństwa na wypadek błędu w logice rozwiązywania (np. literówka w kodzie wpisująca nieistniejące ID).

### 4.3. Decyzja: `NUMERIC` bez wymuszonej skali dla pól cenowych

Pola monetarne w Odoo (`list_price`, `standard_price`) są typu `Monetary`, którego precyzja zależy od `res.currency.decimal_places`, ustalanej w runtime — nie jest stałą wartością do zaszycia w schemacie stagingu. Precyzja/zaokrąglenie stosowane programowo w Etapie 3, przy zapisie do Odoo, nie na poziomie kolumny.

### 4.4. Decyzja: retencja — pełne porzucenie po zamknięciu przebiegu, bez trwałego logu wyjątków

**Ustalone:** po zakończeniu Etapu 3 generowany jest zwięzły raport (patrz 6b), a **cała** zawartość stagingu dla danego `run_id` — zarówno `processed`, jak i `error` — jest kasowana. Nie ma rozróżnienia w cyklu życia między tymi dwoma stanami; oba trwają wyłącznie do wygenerowania raportu.

To zastępuje wcześniej rozważany wzorzec trwałego "logu wyjątków" (`parent_id`/`child_ids`, analogicznie do dzieci `communication.log` w KSeF) — świadomie odrzucony na rzecz raportu generowanego raz, patrz 6b.

Kasowanie 2,12 mln wierszy jedną transakcją byłoby ciężkim `DELETE` (WAL, blokady, potem `VACUUM`) — powinno iść w batchach, tą samą logiką co przetwarzanie (Etap 3, `SKIP LOCKED`), nie jako jeden zbiorczy `DELETE FROM ... WHERE run_id = X`.

## 4a. Indeksy

```sql
-- rezerwacja batcha (SKIP LOCKED, sekcja 8.1) — najgorętsza ścieżka, wykonywana per batch
CREATE INDEX idx_staging_run_state ON hfb_apds_staging_line (run_id, state);

-- odzyskiwanie osieroconych rezerwacji (worker padł po rezerwacji)
CREATE INDEX idx_staging_stale ON hfb_apds_staging_line (state, reserved_at)
    WHERE state = 'processing';

-- jednorazowa detekcja duplikatów EAN po COPY
CREATE INDEX idx_staging_ean ON hfb_apds_staging_line (ean)
    WHERE ean IS NOT NULL AND ean <> '';

-- jednorazowa detekcja duplikatów default_code po COPY
CREATE INDEX idx_staging_default_code ON hfb_apds_staging_line (default_code)
    WHERE default_code IS NOT NULL;
```

Celowo bez indeksu na `odoo_product_id`/`worker_id` — brak zapytania, które by tego dziś potrzebowało; niepotrzebny indeks tylko spowalnia `COPY`/`UPDATE`.

## 5. Porcjowanie

Dane będą przetwarzane w partiach.

Rozważany zakres:

```text
1000–5000 rekordów / batch
```

Batch jest przede wszystkim **jednostką przetwarzania i granicą transakcji**, a nie częstotliwością uruchamiania procesu.

Dla 2,12 mln rekordów:

| Batch | Liczba batchy |
|---:|---:|
| 1 000 | 2 120 |
| 5 000 | 424 |

Ostateczna wielkość batcha powinna wynikać z testów.

## 6. Batch a cron

Nie przyjmujemy modelu:

```text
cron co 1–5 min
→ jeden batch
→ koniec
```

ponieważ przy pełnym pliku prowadziłoby to do bardzo długiego czasu wykonania.

Preferowany model:

```text
uruchomienie procesu
   ↓
przetwarzanie kolejnych batchy
   ↓
commit po batchu
   ↓
kolejny batch
   ↓
...
   ↓
zakończenie procesu
```

Częstotliwość crona nie powinna ograniczać procesu do jednej partii.

### 6.1. Ustalony model: natywne API postępu crona (`_commit_progress`, Odoo 19)

Model "jeden długi proces" musi pogodzić się z twardymi limitami czasu wykonania Odoo (`limit_time_cpu` / `limit_time_real`) — pojedyncze wywołanie crona nie może działać godzinami, zostanie zabite w nieprzewidywalnym miejscu.

**Zweryfikowane bezpośrednio w źródłach `ir_cron.py`, gałąź 19.0** (nie zakładane na podstawie starszych wersji — w 18.0 mechanizm nazywał się inaczej i działał na innej zasadzie): Odoo ma wbudowane API do zgłaszania częściowego postępu zadania crona, `IrCron._commit_progress(processed, remaining)`, zwracające pozostały budżet czasu (`cron_end_time - teraz`). Zastępuje ono przestarzałe (deprecated od 19.0) `_notify_progress`.

Framework sam decyduje o dalszym losie zadania na podstawie zgłoszonego postępu:
- `remaining == 0` → zadanie w pełni ukończone, planowane normalnie,
- `remaining > 0` → zadanie oznaczane jako `PARTIALLY_DONE` i **natychmiast** udostępniane do ponownego podjęcia (`_reschedule_asap`) — bez czekania na regularny harmonogram, bez potrzeby ręcznego `_trigger()`.

Framework sam też powtarza wywołanie naszego callbacku w ramach jednej "tury" workera (`_run_job`, do `MIN_RUNS_PER_JOB=10` razy / `MIN_TIME_PER_JOB=10s`, które z siebie samych) — **dlatego nasz callback nie powinien sam pętlić po wielu batchach**, tylko przetwarzać dokładnie jeden batch na wywołanie i oddawać kontrolę:

```python
def _cron_process_apds_batch(self):
    batch = self._claim_batch()  # SKIP LOCKED, patrz 8.1
    if not batch:
        self.env['ir.cron']._commit_progress(processed=0, remaining=0)
        return
    self._process_batch(batch)
    remaining_count = self._count_draft_rows()
    self.env['ir.cron']._commit_progress(processed=len(batch), remaining=remaining_count)
```

```text
wywołanie crona
   │
   ▼
_claim_batch() (SKIP LOCKED) → przetwórz → _commit_progress(processed, remaining)
   │
   ├─ remaining > 0 → framework: PARTIALLY_DONE → _reschedule_asap (natychmiast, dowolny wolny worker)
   └─ remaining == 0 → framework: FULLY_DONE → planowanie normalne
```

**Nasza odpowiedzialność zawęża się do:** (1) jeden batch na wywołanie callbacku, (2) zgłoszenie postępu przez `_commit_progress`, (3) dopilnowanie, żeby czas przetwarzania *pojedynczego* batcha z osobna mieścił się bezpiecznie w `limit_time_cpu`/`limit_time_real` — `MIN_RUNS_PER_JOB`/`MIN_TIME_PER_JOB` to gwarancja minimalnej pracy przed oddaniem kontroli (sprawiedliwość/wydajność przekazywania), **nie** ochrona przed przekroczeniem twardego limitu czasu procesu.

Efekt: proces zachowuje się z zewnątrz jak ciągłe przetwarzanie (przerwy między porcjami rzędu sekund, nie minut wynikających z harmonogramu), realizowane w całości natywnym mechanizmem crona, bez własnej infrastruktury do samo-wznawiania.

## 6a. Wieloworkerowość: N rekordów `ir.cron`, nie jeden

**Zweryfikowane w źródłach 19.0:** pojedyncze zadanie crona nigdy nie jest przetwarzane przez dwóch workerów naraz — `_acquire_one_job` blokuje konkretny `job_id` (`FOR NO KEY UPDATE SKIP LOCKED ... AND id = %s`), a dyspozytor (`_process_jobs_loop`) przetwarza gotowe zadania **sekwencyjnie** w ramach jednego wywołania. "Natychmiastowe udostępnienie" (`_reschedule_asap`) nie oznacza podjęcia przez kilku workerów naraz — oznacza tylko brak oczekiwania na harmonogram.

**Konsekwencja: żeby uzyskać N faktycznie równoległych workerów, potrzeba N osobnych rekordów `ir.cron`**, każdy niezależnie wywołujący `_cron_process_apds_batch` i niezależnie odpytujący kolejkę stagingu przez `SKIP LOCKED` (8.1) — nie jednego zadania z założeniem, że framework je rozmnoży.

**Twardy sufit równoległości ustawiany jest poza modułem — parametrem serwera:**

```text
--max-cron-threads (domyślnie: 2)
```

Jeśli zdefiniujemy N rekordów `ir.cron` dla APDS, a serwer ma `max_cron_threads` mniejsze niż N, realnie równolegle będzie działać tylko tyle, ile pozwala ten parametr — reszta czeka w kolejce dyspozytora. To wymaga świadomej konfiguracji środowiska docelowego, nie jest czymś, co `hfb_apds` kontroluje z poziomu modułu.

## 6b. Raport końcowy — zwięzły, nie pełna lista

**Problem:** przy dużej skali błędów (rzędu tysięcy/dziesiątek tysięcy) generowanie pełnej listy wyjątków — w dowolnym formacie, PDF czy XLSX — jest kosztowne (czas generowania, obciążenie serwera) i nieproporcjonalne do wartości, jaką daje: nikt nie przegląda ręcznie dziesiątek tysięcy wierszy. Duża liczba błędów tego samego typu to sygnał problemu systemowego ze źródłem, a nie zbiór osobnych przypadków do pojedynczej korekty.

**Ustalone: raport = wyłącznie liczby zbiorcze + ograniczona (bounded) próbka przykładów, nigdy pełna lista.**

- **Liczby zbiorcze per kategoria błędu** — `GROUP BY`/`COUNT` po kategorii (duplikat EAN: N, duplikat `default_code`: M, brak nazwy: K...). Koszt stały, niezależny od liczby błędów — zależy tylko od (małej, stałej) liczby kategorii.
- **Bounded próbka** — np. pierwsze 20-50 wierszy na kategorię (`LIMIT`), wystarczające do zdiagnozowania charakteru problemu (który dostawca, jaki wzorzec), bez próby wypisania wszystkiego. Koszt stały, niezależny od N.
- **Pełnej listy nie generujemy automatycznie w żadnym formacie.**

**Format: PDF, wg istniejącego wzorca QWeb w repozytorium** (`reports/invoice_report.py` + `views/upo_pdf_templates.xml`, użyty już dla raportu UPO z KSeF) — kontynuacja istniejącej konwencji, nie nowa zależność. **XLSX odpada** — skoro nie generujemy pełnej listy, nie ma czego wkładać do arkusza ponad to, co mieści się w zwięzłym PDF; wprowadzenie nowej zależności (`openpyxl`/`xlsxwriter`, nieobecnej dziś w module) nie byłoby uzasadnione.

Raport zapisywany jako `ir.attachment` na rekordzie `communication.log` — standardowy mechanizm Odoo.

**Konsekwencja dla 4.4:** skoro raport nie zawiera pełnej listy, a staging jest w całości kasowany po jego wygenerowaniu — pełna lista błędów nie jest dostępna po zamknięciu przebiegu, w żadnej formie. Świadomie zaakceptowane, spójne z tym, że duże wolumeny błędów tego samego typu są diagnozowane przez same liczby zbiorcze, nie przez przegląd wiersz-po-wierszu.

**Nowa, jeszcze nierozstrzygnięta myśl, zasygnalizowana przy tej okazji, nie decyzja:** wysoki odsetek błędów pojedynczych rekordów mógłby zostać potraktowany jako osobna bramka kontrolna, analogicznie do kontroli % zerowania stanów (`APDS_specyfikacja_wdrozeniowa.md`, sekcja 4) — "jeśli błędy przekraczają X%, to samo w sobie podejrzane" — niezależnie od wyniku kontroli zerowania stanów. Nieustalone, do rozważenia osobno.

## 7. Wielowątkowe przetwarzanie

Ze względu na skalę Etap 3 powinien być realizowany jako **równoległe przetwarzanie niezależnych partii**.

**Doprecyzowane (patrz 6a):** "worker" na poniższym diagramie to konkretnie **osobny rekord `ir.cron`** — nie wątek ani proces zarządzany ręcznie przez APDS. Realna równoległość ograniczona jest parametrem serwera `max_cron_threads`.

```text
                 staging
                    │
               kolejka pracy (SKIP LOCKED)
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
  ir.cron #1    ir.cron #2   ir.cron #3
   (worker 1)    (worker 2)   (worker 3)
       │            │            │
      batch        batch        batch
       │            │            │
       ▼            ▼            ▼
    Odoo          Odoo          Odoo
```

Liczba workerów (= liczba rekordów `ir.cron` dla APDS) nie jest jeszcze ustalona — powinna być dobrana na podstawie testów, z uwzględnieniem `max_cron_threads` środowiska docelowego (patrz 6a).

Przykładowe wartości robocze:

```text
workers = 4
batch_size = 5000
```

nie są jeszcze decyzją produkcyjną.

## 8. Rezerwacja batcha

Worker nie powinien otrzymywać z góry przypisanego stałego zakresu rekordów.

Preferowany model:

```text
staging
   ↓
worker pobiera wolny batch
   ↓
rezerwuje batch
   ↓
przetwarza
   ↓
zatwierdza wynik
```

Dzięki temu po awarii workera jego partia może zostać ponownie udostępniona innemu workerowi.

### 8.1. Ustalony mechanizm rezerwacji

Rezerwacja i pobranie batcha to jedna atomowa operacja SQL, bez centralnego koordynatora:

```sql
UPDATE hfb_apds_staging_line
SET state = 'processing', worker_id = %s, reserved_at = now()
WHERE id IN (
    SELECT id FROM hfb_apds_staging_line
    WHERE state = 'draft'
    LIMIT %s
    FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

`RETURNING *` — nie `raw_data` (kolumna nie istnieje w tabeli, patrz 4.1: staging przechowuje rozbite pola, nie `jsonb`). Worker w Etapie 3 potrzebuje bezpośrednio wartości takich pól jak `default_code`, `name`, `price_sell_netto`, rozwiązanych `categ_id`/`uom_id`/`tax_id` itd. do zbudowania `vals` dla `create()`/`write()` — stąd zwrot całego wiersza, nie pojedynczej kolumny z surowym rekordem.

`FOR UPDATE SKIP LOCKED` gwarantuje, że równolegli workerzy nigdy nie wezmą tego samego wiersza — każdy pomija wiersze aktualnie zablokowane przez innego i bierze następne wolne. Blokada trzymana jest tylko przez czas tej jednej krótkiej operacji UPDATE, nie przez cały czas przetwarzania batcha — długo trzymane blokady w PostgreSQL są niepożądane same w sobie.

Odzyskiwanie osieroconych rezerwacji (worker padł po rezerwacji, przed zapisaniem wyniku) opiera się na `reserved_at` + progu czasowym — wiersz `processing` starszy niż próg wraca do `draft`. Konkretna wartość progu nie jest jeszcze ustalona — patrz sekcja 13.

## 9. Stan rekordu staging

Dla potrzeb wznowienia rekord staging powinien posiadać stan przetwarzania.

Rozważane stany:

```text
draft
processing
processed
error
```

Stan `processing` jest istotny przy pracy wielu workerów. Sam model `draft → processed` nie wystarcza do rozróżnienia rekordu oczekującego od aktualnie przetwarzanego.

**Ustalone:** rezerwacja i faktyczne przetwarzanie to ten sam stan (`processing`), rozróżniany atrybutami `worker_id` i `reserved_at`, a nie dwoma osobnymi stanami — prostszy z dwóch rozważanych wariantów.

Semantyka stanów oraz odzyskiwanie rekordów pozostających w `processing` po awarii wymagają dalszego zaprojektowania (patrz 8.1) — kierunek znany (próg czasowy na `reserved_at`), konkretna wartość progu nie.

## 10. Granica transakcji

Każdy batch jest niezależną jednostką transakcyjną — awaria podczas przetwarzania nie powinna wymagać ponownego wykonania całego pliku, tylko tego jednego batcha.

### 10.1. Trzy zasady rozstrzygające granicę błędu wewnątrz batcha

**Zasada rozstrzygająca:** jeżeli błąd można jednoznacznie przypisać do konkretnego rekordu danych, jest to błąd rekordu. Jeżeli nie można go przypisać do konkretnego rekordu, traktujemy go jako błąd infrastrukturalny.

1. **Błąd rekordu** → `SAVEPOINT` cofnięty do stanu sprzed rekordu → wiersz stagingu oznaczony `error` → przetwarzanie kontynuowane od następnego rekordu, w tej samej transakcji batcha.
2. **Deadlock / konflikt współbieżności** → **nie jest błędem danych** → wyjątek od zasady 1, mimo że technicznie jest przypisywalny do konkretnej operacji. Retry na poziomie rekordu (nie od razu całego batcha) — patrz 10.3.
3. **Błąd infrastrukturalny** (nieprzypisywalny do rekordu — np. utrata połączenia z bazą, brak miejsca na dysku) → `ROLLBACK` całej transakcji batcha → batch wraca do kolejki (`state` z powrotem na `draft`) do ponowienia, albo zatrzymanie procesu, zależnie od charakteru awarii.

```text
batch
 │
 ├── rekord OK      → RELEASE SAVEPOINT
 ├── rekord ERROR   → ROLLBACK TO SAVEPOINT → staging.state='error' → następny rekord
 ├── rekord DEADLOCK → ROLLBACK TO SAVEPOINT → retry tego samego zapisu (patrz 10.3)
 │
 └── COMMIT batcha
```

```text
błąd infrastrukturalny w trakcie batcha
 │
 ▼
ROLLBACK całej transakcji batcha
 │
 ▼
batch → 'draft' (do ponowienia) / zatrzymanie workera lub procesu
```

### 10.2. Koszt rollbacku batcha — świadomy kompromis, nie wada

`RELEASE SAVEPOINT` scala zmiany rekordu do transakcji zewnętrznej, ale ich nie commituje na dysk. Błąd infrastrukturalny w środku batcha (np. na rekordzie 4000 z 5000) cofa `ROLLBACK`-iem **również** rekordy przetworzone poprawnie wcześniej w tym samym batchu, nie tylko ten, który zawiódł. Idempotencja (10.4) sprawia, że ponowne przetworzenie tych rekordów daje ten sam wynik — nic się nie psuje merytorycznie — ale to realny koszt wydajnościowy.

**Wielkość batcha jest więc kompromisem:**
- większy batch → wyższa wydajność (mniej narzutu na commit/rezerwację),
- większy batch → większy koszt potencjalnie utraconej pracy przy rzadkim błędzie infrastrukturalnym pod jego koniec.

Dodatkowy czynnik przy ustalaniu docelowej wielkości batcha (patrz sekcja 13, punkt 5), obok wcześniej wskazanego ograniczenia czasowego pojedynczego wywołania.

### 10.3. Deadlock — zweryfikowane jako odzyskiwalny na poziomie savepointa

Zweryfikowane względem dokumentacji PostgreSQL: mechanizm odzyskiwania po błędzie (`ROLLBACK TO SAVEPOINT`) jest **generyczny względem typu błędu** — to ten sam mechanizm, na którym oparte są bloki `EXCEPTION` w PL/pgSQL, a `deadlock_detected` jest zwykłym kodem błędu jak każdy inny. Dodatkowo: **blokady założone po ustanowieniu savepointa są zwalniane natychmiast przy rollbacku do niego** (dokumentacja PostgreSQL, „Explicit Locking") — więc wycofanie rekordu do savepointa realnie zwalnia sporny zasób przed ponowną próbą.

**Wniosek:** deadlock nie wymaga eskalacji do rollbacku całego batcha — może być obsłużony jako `ROLLBACK TO SAVEPOINT` + ponowna próba tego samego zapisu, w tej samej transakcji batcha.

**Otwarty parametr, nierozstrzygnięty:** liczba prób na poziomie rekordu, zanim deadlock zostanie potraktowany jako błąd poważniejszy (eskalacja do błędu infrastrukturalnego / rollbacku batcha).

### 10.4. Idempotencja jako warunek konieczny, nie optymalizacja

Przetworzenie tego samego rekordu źródłowego dowolną liczbę razy powinno prowadzić do tego samego stanu danych Odoo. To jest **warunek konieczny** całego mechanizmu wznowienia — nie dodatkowa optymalizacja — bo opierają się na nim dwa niezależne mechanizmy ponowienia:

1. **błąd rekordu** — `SAVEPOINT` → rollback → `error`, przetwarzanie kontynuowane,
2. **awaria workera** — batch pozostaje w `processing`/wraca do `draft` → ponowne przetworzenie całego batcha od nowa.

### 10.5. Model find-or-create — uproszczony, do doprecyzowania po 9.5

```text
                    staging
                       │
                       ▼
                  default_code
                       │
                       ▼
              find product.template
                 /              \
              istnieje          brak
                 │                 │
                 ▼                 ▼
           vals_update        vals_create
                 │                 │
                 └────────┬────────┘
                          ▼
                     processed
```

**Świadome uproszczenie, nie ostateczny kształt:** `vals_update` i `vals_create` mogą docelowo się różnić, jeśli zostanie przyjęta polityka pól typu `create_only` (pole ustawiane tylko przy tworzeniu, nie nadpisywane przy aktualizacji — rozważane dla `name`, `APDS_projekt.md` 9.5). Struktura techniczna workera celowo nie jest wiązana z tą jeszcze niezamkniętą polityką biznesową — gałąź `find-or-create` pozostaje poprawna niezależnie od tego, jak 9.5 zostanie ostatecznie rozstrzygnięte; zmieni się tylko zawartość `vals_update`.

### 10.6. Techniczna podstawa — zweryfikowana w źródłach Odoo 19.0

`Cursor.savepoint()` (`odoo/sql_db.py`) — natywny mechanizm, nie surowe SQL na `env.cr`. Domyślnie (`flush=True`) używa `_FlushingSavepoint`, wymuszając `cr.flush()` (zapisanie oczekujących zmian ORM-a do SQL) **przed** utworzeniem `SAVEPOINT` — zapewnia spójność między pamięcią Pythona a transakcją SQL na wejściu.

**Nierozstrzygnięte, wymaga weryfikacji na etapie prototypu:** czy po `ROLLBACK TO SAVEPOINT` framework automatycznie unieważnia cache rekordów w pamięci (`env.invalidate_all()` lub podobne), czy to obowiązek wywołującego kodu. Nie znaleziono na to dowodu w dostępnym wycinku źródeł — nie zakładać żadnej odpowiedzi bez sprawdzenia na konkretnym przykładzie (utworzyć rekord w savepoincie, wycofać, sprawdzić spójność `env`).

## 11. Podział odpowiedzialności etapów

### Etap 2 — Przygotowanie do przetwarzania

```text
7z
 ↓
JSON (parser streamingowy)
 ↓
rekord źródłowy
 ↓
XET node (src_rel_path → wartość / value_expr → transformacja)
 ↓
COPY
 ↓
staging (rozbite pola)
 ↓
kontrole
```

### Etap 3 — Przetwarzanie

```text
staging
 ↓
batch
 ↓
worker
 ↓
Odoo
```

Mapowanie XET jest rozwiązywane raz, w Etapie 2 (patrz 4, 12) — Etap 3 czyta już gotowe, rozbite wartości ze stagingu, bez ponownego wykonywania `src_rel_path`/`value_expr` w pętli workerów.

Wielowątkowość dotyczy przede wszystkim Etapu 3.

## 12. XET

APDS wykorzystuje istniejące mechanizmy XET Base — kierunek potwierdzony, szczegóły częściowo doprecyzowane.

Szablon XET (`xml.export.template`/`xml.export.node`) definiuje mapowanie danych poprzez drzewo węzłów, z dwoma trybami wartości:

- **`src_rel_path`** (pole `Char`, np. `"price.sell.netto"`) — prosta ścieżka do wartości. Bez odwróconej semantyki względem kierunku (import/eksport) — to zwykły tekstowy zapis ścieżki, neutralny co do kierunku.
- **`value_expr`** — wyrażenie `safe_eval`, ewaluowane w dowolnym przekazanym kontekście (nie tylko na rekordach Odoo) — do logiki warunkowej i transformacji.

**Ustalony podział pracy:** `src_rel_path` do prostych, bezpośrednich ścieżek (`price.sell.netto`, `stock.total`); `value_expr` tam, gdzie potrzebna logika warunkowa (flagi→tagi, `stock.total==0`→pomiń rekord).

**Ustalony moment rozwiązywania (patrz 4, 11): w Etapie 2**, raz, podczas strumieniowego przetwarzania pliku — nie ponownie w pętli workerów w Etapie 3. To eliminuje pytanie o wydajność `safe_eval` w gorącej pętli równoległych workerów — koszt ewaluacji mapowania ponoszony jest raz, przy tworzeniu stagingu, nie 2,12 mln razy w Etapie 3.

**Wymagana adaptacja techniczna — `_resolve_relpath` w obecnej postaci nie działa na JSON-ie.** Metoda zaczyna się od `record._name` (wywali się na zwykłym `dict`) i używa `hasattr`/`getattr` (dostęp atrybutowy, nie kluczowy). Wymagany wariant świadomy obu przypadków:

```python
def _resolve_relpath_json(self, record, rel_path):
    if not rel_path or not rel_path.strip():
        return None
    result = record
    for field in rel_path.split('.'):
        if isinstance(result, dict):
            if field not in result:
                return None
            result = result[field]
        else:
            if not hasattr(result, field):
                return None
            result = getattr(result, field)
        if result is None:
            return None
    return result
```

**Dwa dodatkowe zastrzeżenia do oryginalnej implementacji:**
- Opis pola `src_rel_path` wspomina obsługę indeksów tablicowych (`"Indeksy w nawiasach []"`), ale **nie jest to faktycznie zaimplementowane** w `_resolve_relpath` (tylko `.split('.')`, brak parsowania `[n]`). Nieistotne na razie — dotyczy pól tablicowych (`images[]`, `compatibility[]`), obecnie poza zakresem prototypu — ale nie polegać na tym bez dodania.
- Oryginalna metoda loguje niemal każdy krok (`_logger.info`) — akceptowalne przy jednym dokumencie KSeF na raz, nieakceptowalne przy 2,12 mln wywołań w Etapie 2. Wariant APDS wymaga wyciszenia tego logowania.

**Nierozstrzygnięte:** czy APDS reużywa dosłownie model `xml.export.node` (koszt: pola specyficzne dla XSD pozostają niewykorzystane, martwe w formularzu), czy nowy, lżejszy model inspirowany tym samym pomysłem (drzewo + `src_rel_path`/`value_expr`), analogicznie do tego, jak KSeF nie rozciągał generycznego `state` na `communication.log`, tylko dokładał własne, poprawnie nazwane pola.

Niezależnie od wyboru: worker (Etap 3) nie ma zakodowanego na stałe mapowania poszczególnych pól JSON → Odoo — czyta już rozbite, gotowe wartości ze stagingu.

## 12a. Reużycie pól konfiguracyjnych `communication.provider`

Bazowy model `communication.provider` ma już pola pod harmonogramowanie i batching — sprawdzone pod kątem przydatności dla APDS:

**Reużywalne wprost, bez zmian:**
- `cron_id` (Many2one do `ir.cron`) — punkt zaczepienia dla samo-wyzwalania z sekcji 6.1 (`self.cron_id._trigger()`).

**Ten sam mechanizm, potrzebna nowa wartość (`selection_add`, wzorem rozszerzeń `state` w KSeF):**
- `trigger_type` ("Tryb wysyłki") — istniejące wartości (`manual`, `immediate`, `on_document_validate`, `on_document_post`, `cron_scheduled`, `batch_nightly`) nie opisują modelu z sekcji 6.1. Potrzebna nowa wartość, roboczo `apds_streaming`, dopisana przez `selection_add`. Etykieta pola ("Tryb wysyłki") myląca dla kontekstu importu — do rozważenia przy `communication.provider.apds`.

**Ta sama nazwa, inna semantyka — NIE reużywać, potrzebne nowe pola na `communication.provider.apds`:**
- `batch_time` — oznacza godzinę zegarową uruchomienia (`"02:00"`), nie ma odpowiednika potrzebnego w modelu z sekcji 6.1 — budżet czasu wywołania jest teraz zarządzany natywnie przez framework (`MIN_RUNS_PER_JOB`/`MIN_TIME_PER_JOB`, `_commit_progress`), nie własnym polem konfiguracyjnym. `batch_time` pozostaje nieprzydatne dla APDS niezależnie od tego.
- `batch_size` (domyślnie 50, opisane pod kątem dokumentów KSeF) — inny rząd wielkości niż potrzeby APDS (setki/tysiące rekordów). Potrzebne osobne pole na `communication.provider.apds`.

**Nie jest polem konfiguracyjnym providera [ZMIANA względem wcześniejszych ustaleń]:** "rodzaj przebiegu" (pełny/różnicowy) — pierwotnie zakładany jako stałe pole konfiguracyjne, teraz nieaktualne. Ustalone z klientem (`APDS_projekt.md`, sekcja 3/6.0): plik źródłowy jest zawsze ten sam, jego zawartość zmienia się między wariantem pełnym a różnicowym. Rodzaj przebiegu jest więc **wynikiem rozpoznania zawartości per przebieg** (`APDS_projekt.md`, [9.9](../APDS_projekt.md#99-rozpoznawanie-rodzaju-zawartości-pliku), mechanizm nieustalony), zapisywanym na `communication.log`, nie ustawianym raz na `communication.provider.apds`.



1. ~~Ostateczna pełna lista kolumn `hfb_apds_staging_line`~~ — **rozstrzygnięte, patrz 4.1** (pełny DDL).
2. ~~Czy staging przechowuje surowy `jsonb`, rozbite pola, czy rozwiązanie mieszane~~ — **rozstrzygnięte, patrz 4/11/12: rozbite pola**, mapowanie XET rozwiązywane w Etapie 2.
3. Dokładny mechanizm streamingu JSON.
4. Sposób przekazywania danych do `COPY FROM STDIN`.
5. Wielkość batcha (nowe pole `apds_batch_size` na `communication.provider.apds` — patrz 12a; sama wartość nieustalona).
6. Liczba workerów (= liczba rekordów `ir.cron` — mechanizm ustalony, patrz 6a; konkretna liczba nieustalona, zależna też od `max_cron_threads` środowiska docelowego).
7. ~~Mechanizm rezerwacji batcha~~ — **rozstrzygnięte, patrz 8.1** (`UPDATE ... FOR UPDATE SKIP LOCKED`).
8. Odzyskiwanie rekordów pozostających w `processing` — **kierunek znany** (próg czasowy na `reserved_at`, patrz 8.1/9), **konkretna wartość progu nieustalona**.
9. ~~Raportowanie błędów pojedynczych rekordów~~ — **rozstrzygnięte, patrz 6b**: raport PDF, liczby zbiorcze + bounded próbka, bez pełnej listy, bez XLSX.
10. ~~Szczegółowa granica transakcji~~ — **rozstrzygnięte, patrz 10.1-10.3**: trzy zasady (błąd rekordu / deadlock / błąd infrastrukturalny), SAVEPOINT per rekord.
11. ~~Idempotencja przy ponowieniu batcha~~ — **ustalona jako warunek konieczny mechanizmu wznowienia, patrz 10.4**, nie dodatkowa optymalizacja.
12. Sposób zakończenia procesu po przetworzeniu wszystkich batchy.
13. ~~Budżet czasu jednego wywołania crona~~ — **zastąpione natywnym mechanizmem `_commit_progress`/`_reschedule_asap` (patrz 6.1)**; nadal do ustalenia: docelowy rozmiar pojedynczego batcha tak, by mieścił się bezpiecznie w `limit_time_cpu`/`limit_time_real` środowiska docelowego (to zależy od wydajności etapu 3, wymaga pomiaru).
14. Reuse dosłowny vs nowy model dla mapowania XET (patrz sekcja 12) — kierunek (drzewo + `src_rel_path`/`value_expr`) i moment rozwiązywania (Etap 2) potwierdzone, wybór konkretnego modelu nieustalony.
15. Wydajność `src_rel_path`/`value_expr` przy skali 2,12 mln rekordów × N pól w Etapie 2 — pytanie przesunięte z Etapu 3 (worker) do Etapu 2 (streaming), wciąż wymaga pomiaru, ale przestało dotyczyć równoległości.
16. Obsługa indeksów tablicowych w `src_rel_path` (`images[0]`) — opisana w polu, niezaimplementowana w kodzie; nieistotne, dopóki pola tablicowe pozostają poza zakresem prototypu.
17. Bramka kontrolna dla wysokiego odsetka błędów pojedynczych rekordów, analogiczna do kontroli % zerowania stanów (patrz 6b) — zasygnalizowane, nierozstrzygnięte, nieprzeanalizowane.
18. Liczba prób ponowienia rekordu na poziomie savepointa przy deadlocku, zanim nastąpi eskalacja (patrz 10.3) — nieustalona.
19. Zachowanie cache rekordów Odoo (`env`) po `ROLLBACK TO SAVEPOINT` — czy wymaga jawnej inwalidacji (patrz 10.6) — nieustalone, wymaga weryfikacji na prototypie.

## 14. Prototyp

Pierwszy prototyp powinien zweryfikować:

```text
7z
 ↓
streaming JSON
 ↓
COPY
 ↓
PostgreSQL staging
```

Następnie należy przetestować różne kombinacje wielkości batcha i liczby workerów.

Podstawowe pomiary:

- czas importu JSON do staging,
- zużycie RAM,
- wielkość tabeli staging,
- przepustowość importu,
- czas przetwarzania batcha,
- przepustowość workerów,
- wpływ liczby workerów na PostgreSQL i Odoo,
- zachowanie po awarii workera.

Wyniki prototypu będą podstawą do zamknięcia parametrów technicznych rozwiązania produkcyjnego.

## 15. Status

**Status: roboczy.**

Dokument zawiera wyłącznie ustalenia poczynione na obecnym etapie oraz jawnie oznaczone kwestie otwarte.

Uzupełniono o: model budżetu czasowego wywołania crona z samo-wyzwoleniem (6.1), mechanizm rezerwacji batcha przez `SKIP LOCKED` (8.1), potwierdzenie połączonego stanu `processing` (9), rozstrzygnięcie momentu rozwiązywania mapowania XET — Etap 2, nie Etap 3 (11, 12), rozstrzygnięcie struktury stagingu na rozbite pola (4), analizę reużycia pól `communication.provider` (12a).

**Korekta po weryfikacji źródeł Odoo bezpośrednio na gałęzi 19.0:** pierwotna propozycja z 6.1 (ręczny budżet czasu + `_trigger()`) zastąpiona natywnym mechanizmem crona (`_commit_progress`, `_reschedule_asap`) — prostsze, wbudowane, nie wymaga własnej infrastruktury do samo-wznawiania. Dodano sekcję 6a: wieloworkerowość wymaga N osobnych rekordów `ir.cron`, nie jednego zadania — z twardą zależnością od parametru serwera `max_cron_threads` (domyślnie 2).

**Kolejne uzupełnienie:** pełny DDL `hfb_apds_staging_line` wraz z indeksami (4.1, 4a), decyzje o rezygnacji z rzeczywistych FK dla pól rozwiązywanych własnym kodem (4.2), o precyzji pól cenowych ustalanej programowo (4.3), o pełnym porzuceniu stagingu po zamknięciu przebiegu bez trwałego logu wyjątków (4.4) oraz o kształcie raportu końcowego — PDF, liczby zbiorcze + bounded próbka, bez pełnej listy, bez formatu XLSX (6b).

**Kolejne uzupełnienie:** pełny model transakcyjności Etapu 3 (sekcja 10) — trzy zasady granicy błędu (rekord / deadlock / infrastruktura), `SAVEPOINT` per rekord z natywnym `Cursor.savepoint()` Odoo, deadlock zweryfikowany jako odzyskiwalny na poziomie savepointa (nie wymaga rollbacku całego batcha), koszt rollbacku batcha jako świadomy kompromis wielkości batcha, idempotencja jako warunek konieczny (nie optymalizacja), model `find-or-create` z rozdzielonymi `vals_create`/`vals_update` celowo niewiązanymi z nierozstrzygniętą jeszcze polityką pól (9.5).

# hfb_apds

## ALIAS Product Data Synchronization (APDS)

Moduł Odoo 19 Community Edition odpowiedzialny za kontrolowaną synchronizację danych produktów dostarczanych przez zewnętrzne źródło danych.

**Proces:** ALIAS Product Data Synchronization (APDS)  
**Moduł:** `hfb_apds`  
**Platforma:** Odoo 19 CE  
**Baza integracyjna:** XET Base (`hfb_xmlmap_exporter`)

## Cel

APDS służy do kontrolowanej synchronizacji danych produktów pomiędzy zewnętrznym źródłem danych a Odoo.

Proces ma obsługiwać pełną i różnicową synchronizację, automatyczne uruchamianie procesu, możliwość ponowienia przebiegu, kontrolę danych przed ich zastosowaniem, przetwarzanie dużych zbiorów danych, równoległe przetwarzanie oraz rejestrację przebiegu i wyniku synchronizacji.

Docelowa skala pełnego zbioru danych wynosi około 2,12 mln rekordów produktów.

## Architektura procesu

Jeden rekord `communication.log` reprezentuje jeden przebieg procesu APDS.

Proces jest uruchamiany przez dwa niezależne crony:

```text
CRON 1 — raz dziennie
        |
        v
communication.provider.apds
        |
        v
_cron_apds_init()
        |
        v
communication.log
        |
        v
apds_operation = download

CRON 2 — cyklicznie
        |
        v
communication.log
        |
        v
_cron_apds_process()
        |
        +-- download
        +-- prepare
        +-- process
```

Cron inicjujący wyłącznie tworzy nowy rekord `communication.log`. Nie wykonuje pobrania ani dalszego przetwarzania.

Cron przetwarzający realizuje kolejne operacje przebiegu zgodnie z bieżącą wartością `apds_operation`.

## Operacje APDS

Bieżąca operacja jest przechowywana w `communication.log` w polu `apds_operation`.

Podstawowy przebieg:

```text
download
    |
    v
prepare
    |
    v
process
    |
    v
completed
```

W przypadku błędu przebieg może zostać zakończony operacją `failed`.

`apds_stage` opisuje etap procesu APDS, natomiast `apds_operation` wskazuje operację przeznaczoną do wykonania.

Bazowe pola `state` i `status` modelu `communication.log` zachowują znaczenie nadane im przez XET Base.

## Etap 1 — pobranie pliku

Etap 1 odpowiada za pobranie pliku źródłowego z SFTP.

Docelowy przepływ:

```text
SFTP
  |
  v
sprawdzenie dostępności
  |
  v
pobranie pliku
  |
  v
kontrola kompletności
```

Implementacja techniczna:

`models/communication_provider_apds_stage_download.py`

Pobranie pliku jest odrębnym etapem i nie obejmuje przygotowania JSON ani synchronizacji produktów.

## Etap 2 — przygotowanie danych

Etap 2 przygotowuje pobrany plik do właściwego przetwarzania.

Docelowy przepływ:

```text
7zip
  |
  v
JSON
  |
  v
parser streamingowy
  |
  v
mapowanie XET
  |
  v
kontrola danych
  |
  v
PostgreSQL COPY
  |
  v
staging
```

Dane powinny być przetwarzane strumieniowo, bez ładowania całego pliku JSON do pamięci.

Implementacja techniczna:

`models/communication_provider_apds_stage_prepare.py`

## Staging

`apds.staging.line` jest techniczną tabelą pomocniczą pomiędzy przygotowaniem danych a ich właściwym przetwarzaniem.

Staging służy do:

- porcjowania danych,
- równoległego przetwarzania,
- rezerwacji partii,
- wznowienia procesu,
- obsługi retry,
- przechowywania technicznego stanu rekordów.

`apds.staging.line` nie jest przeznaczony do obsługi przez użytkownika i nie posiada elementów interfejsu użytkownika.

## Etap 3 — przetwarzanie danych

Etap 3 pobiera przygotowane rekordy ze stagingu i synchronizuje je z Odoo.

Docelowy przepływ:

```text
staging
   |
   v
batch
   |
   v
worker
   |
   v
product.template
```

Przetwarzanie powinno odbywać się partiami. Przy równoległej pracy workerów partie powinny być rezerwowane z wykorzystaniem mechanizmu `FOR UPDATE SKIP LOCKED`.

Implementacja techniczna:

`models/communication_provider_apds_stage_process.py`

Etap 3 nie powinien ponownie wykonywać mapowania danych źródłowych.

## Transakcyjność i retry

Batch jest jednostką przetwarzania.

Błąd pojedynczego rekordu nie powinien zatrzymywać całego przebiegu. Dla takich błędów przewidziana jest obsługa z użyciem savepointów.

Błędy infrastrukturalne mogą powodować wycofanie całego batcha i jego ponowienie.

Proces powinien być idempotentny, aby ponowne wykonanie tej samej operacji nie powodowało niepożądanych zmian.

## Wynik przebiegu

`communication.log.apds_result` opisuje wynik całego przebiegu.

Dostępne wyniki:

- `pending` — przebieg jest w trakcie,
- `accepted` — przebieg zakończył się poprawnie i nowy stan może stać się stanem referencyjnym,
- `manual` — przebieg wymaga interwencji ręcznej; poprzedni poprawny stan pozostaje obowiązujący,
- `error` — przebieg zakończył się błędem krytycznym.

## Kontrola danych

Przed zastosowaniem danych przewidziana jest kontrola:

- poprawności pobrania,
- poprawności archiwum,
- struktury JSON,
- kompletności danych,
- wymaganych wartości,
- duplikatów,
- danych biznesowych.

Szczególnej kontroli wymaga sytuacja, w której duża część rekordów wskazuje wyzerowanie stanów magazynowych.

## communication.log

`communication.log` jest centralnym rekordem przebiegu APDS.

Przechowuje informacje dotyczące m.in.:

- providera,
- etapu APDS,
- bieżącej operacji,
- rodzaju synchronizacji,
- wersji danych,
- wyniku procesu,
- liczby rekordów,
- liczby rekordów przetworzonych,
- liczby rekordów utworzonych,
- liczby rekordów zaktualizowanych,
- liczby rekordów pominiętych,
- liczby błędów.

Logi APDS są dostępne w bazowym rejestrze logów komunikacji za pomocą filtra **Logi APDS**.

## Integracja z XET Base

APDS wykorzystuje istniejącą infrastrukturę XET Base, w szczególności:

```text
communication.provider
communication.log
```

Provider APDS jest konfigurowany jako provider typu `apds`.

Warstwa zarządzająca i inicjująca przebieg znajduje się w:

`models/communication_provider_apds.py`

Przetwarzanie istniejących logów znajduje się w:

`models/communication_log.py`

Techniczna realizacja etapów jest rozdzielona na osobne moduły `stage_*`.

## Struktura modułu

```text
hfb_apds/
|
+-- models/
|   +-- communication_provider.py
|   +-- communication_provider_apds.py
|   +-- communication_log.py
|   +-- apds_staging_line.py
|   +-- communication_provider_apds_stage_download.py
|   +-- communication_provider_apds_stage_prepare.py
|   +-- communication_provider_apds_stage_process.py
|
+-- data/
|   +-- ir_cron.xml
|
+-- security/
|   +-- ir.model.access.csv
|
+-- views/
|   +-- communication_provider_views.xml
|   +-- communication_provider_apds_views.xml
|   +-- communication_provider_apds_menu.xml
|   +-- communication_log_views.xml
|
+-- static/
|   +-- description/
|       +-- index.html
|
+-- README.md
```

## Cron

APDS wykorzystuje dwa zadania cron.

### Inicjowanie

`_cron_apds_init()` znajduje się w `communication_provider_apds.py`.

Jest uruchamiany raz dziennie i tworzy nowy `communication.log` z początkową operacją:

```text
apds_operation = download
```

### Przetwarzanie

`_cron_apds_process()` znajduje się w `communication_log.py`.

Jest uruchamiany cyklicznie i przetwarza przebiegi, dla których istnieje operacja wymagająca wykonania.

## Konfiguracja workerów cron

Środowisko wdrożeniowe przewiduje:

```ini
; CRON
max_cron_threads = 10
limit_time_worker_cron = 0
;
```

`max_cron_threads = 10` określa liczbę workerów przeznaczonych do zadań cron.

`limit_time_worker_cron = 0` oznacza brak limitu czasu życia workera cron.

## Aktualny stan implementacji

Moduł posiada strukturę providera APDS, rozszerzenie `communication.log`, staging oraz rozdzielone moduły technicznych etapów procesu.

Zdefiniowane są punkty wejścia:

```text
_cron_apds_init()
_cron_apds_process()

_apds_stage_download()
_apds_stage_prepare()
_apds_stage_process()
```

Techniczna realizacja etapów jest rozwijana etapami.

Do implementacji i testów pozostają w szczególności:

- pobieranie SFTP,
- obsługa archiwum 7zip,
- streaming JSON,
- mapowanie danych,
- PostgreSQL `COPY`,
- staging,
- rezerwacja batchy,
- `FOR UPDATE SKIP LOCKED`,
- przetwarzanie produktów,
- retry,
- idempotencja,
- ocena wyniku zbiorczego,
- zakończenie przebiegu,
- czyszczenie stagingu.

## Testy wydajnościowe

Przed ustaleniem parametrów produkcyjnych należy zmierzyć:

- czas importu JSON do staging,
- zużycie RAM,
- wielkość stagingu,
- przepustowość importu,
- czas przetwarzania batcha,
- przepustowość workerów,
- wpływ liczby workerów na PostgreSQL,
- wpływ liczby workerów na Odoo,
- zachowanie po awarii workera,
- czas pełnej synchronizacji.

Wyniki testów powinny określić m.in. wielkość batcha i rzeczywistą liczbę równoległych workerów.

## Status projektu

**Rozwój / implementacja szkieletu procesu.**

Architektura procesu jest określona:

```text
inicjowanie przebiegu
        |
        v
communication.log
        |
        v
bieżąca operacja
        |
        v
etap techniczny
        |
        v
staging
        |
        v
batch / worker
        |
        v
produkty Odoo
```


Copyright 2017–2026 Hadron for business sp. z o.o.

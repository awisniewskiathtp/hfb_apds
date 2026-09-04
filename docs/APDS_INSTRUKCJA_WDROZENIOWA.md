# APDS — instrukcja wdrożeniowa, konfiguracja i diagnostyka

**Projekt:** ALIAS Product Data Synchronization (APDS)  
**Moduł:** `hfb_apds`  
**Platforma:** Odoo 19 Community Edition  
**Baza integracyjna:** XET Base (`hfb_xmlmap_exporter`)  
**Status:** prototyp do testów klienta  
**Data:** 2026-09-04

## 1. Cel

Dokument opisuje instalację, konfigurację, uruchomienie oraz podstawową diagnostykę prototypu APDS.

Pełny przebieg:

```text
źródło danych
    ↓
Etap 1 — pobranie
    ↓
Etap 2 — przygotowanie
    ↓
apds_staging_line
    ↓
Etap 3 — równoległe przetwarzanie
    ↓
product.template
    ↓
finalizacja
```

Jeden `communication.log` reprezentuje jeden przebieg APDS.

## 2. Wymagania

- Odoo 19 CE
- `hfb_xmlmap_exporter` (XET Base)
- PostgreSQL
- działające workery Odoo
- narzędzie do obsługi archiwum 7zip
- dostęp do źródła danych klienta

Pełny zbiór klienta ma obecnie około 2,12 mln rekordów i około 2,2 GB danych.


### Sugerowana minimalna konfiguracja sprzętowa

Dla pracy APDS z równoległym przetwarzaniem danych zalecana jest następująca konfiguracja:

| Zasób | Minimum | Rekomendowane |
|---|---:|---:|
| CPU | 8 vCPU | 12+ vCPU |
| RAM | 12 GB | 16+ GB |
| SWAP | 2 × RAM | 2 × RAM |

Konfiguracja minimalna pozwala na uruchomienie równoległego przetwarzania APDS z wykorzystaniem wielu workerów Odoo, natomiast konfiguracja rekomendowana zapewnia większy zapas zasobów podczas równoległego przetwarzania dużych zbiorów danych.

**Uwaga:** wymagania dotyczą całego środowiska Odoo/PostgreSQL, a nie wyłącznie procesu APDS. Ostateczna liczba workerów oraz `batch_size` powinna zostać dobrana na podstawie testów wydajnościowych na konkretnym środowisku.

## 3. Instalacja

Repozytorium projektu:

https://github.com/awisniewskiathtp/hfb_apds

Moduł należy umieścić w katalogu znajdującym się w `addons_path`, np.:

```text
/odoo/custom/addons/hfb_apds
```

Po instalacji modułu należy zrestartować Odoo, zaktualizować listę aplikacji i zainstalować `ALIAS Product Data Synchronization`.

Wymagany jest moduł `hfb_xmlmap_exporter`.

Do testów należy używać konkretnego commitu przekazanego klientowi.

## 4. Konfiguracja providera APDS

Konfiguracja obejmuje:

- nazwę,
- kod,
- firmę,
- aktywność,
- kanał źródłowy,
- adres źródła,
- katalog roboczy,
- rozmiar batcha.

### Katalog roboczy

Domyślnie:

```text
/tmp/apds
```

Sprawdzenie:

```bash
ls -ld /tmp/apds
test -w /tmp/apds && echo WRITABLE
```

### Źródło HTTP

Sprawdzenie:

```bash
curl -I "<URL>"
```

Oczekiwany jest poprawny kod HTTP, zwykle `200`.

### Źródło SFTP

Jeżeli używane jest SFTP:

```bash
sftp -P <port> <user>@<host>
```

Nie należy zapisywać haseł ani kluczy prywatnych w dokumentacji lub historii powłoki.

### Batch

Parametr:

```text
apds_batch_size
```

Wartość używana w prototypie jest parametrem testowym. Nie należy traktować jej jako ostatecznej wartości produkcyjnej.

## 5. Workery

Etap 3 może działać równolegle na wielu workerach.

Przykładowe środowisko testowe:

```ini
workers = 10
max_cron_threads = 10
limit_time_worker_cron = 0
```

Liczba workerów powinna być dobrana do środowiska i zweryfikowana pomiarem.

Po zmianie konfiguracji Odoo należy zrestartować usługę.

## 6. Cron

APDS wykorzystuje:

### Inicjowanie

`_cron_apds_init()` tworzy nowy `communication.log` z:

```text
apds_operation = download
```

### Przetwarzanie

`_cron_apds_process()` realizuje kolejne operacje przebiegu.

Podstawowy przebieg:

```text
download → prepare → process → completed
```

## 7. Uruchomienie testu

Po uruchomieniu należy obserwować `communication.log`.

Kontrolować:

- Etap APDS,
- Bieżącą operację APDS,
- Wynik APDS,
- stan,
- komunikaty w chatterze.

Poprawny przebieg powinien przejść przez:

```text
Etap 1 — Pobranie pliku
Etap 2 — Przygotowanie danych
Etap 3 — Przetwarzanie
Zakończono
```

## 8. Etap 1 — diagnostyka

Przy problemie z pobraniem sprawdzić:

```bash
curl -I "<URL>"
```

lub:

```bash
sftp -P <port> <user>@<host>
```

Zweryfikować:

- dostępność hosta,
- port,
- DNS,
- firewall,
- dane uwierzytelniające,
- ścieżkę pliku.

Dla pobranego pliku:

```bash
ls -lh /tmp/apds
```

Nie należy przechodzić do dalszego przetwarzania pliku, którego kompletność jest wątpliwa.

## 9. Etap 2 — diagnostyka

Etap 2 realizuje:

```text
7zip → JSON → streaming → mapowanie → COPY → staging
```

Log Odoo:

```bash
tail -f /var/log/odoo/odoo-server.log
```

Logi APDS:

```bash
grep '\[APDS\]' /var/log/odoo/odoo-server.log | tail -100
```

Przy błędzie należy ustalić, czy problem dotyczy:

- rozpakowania,
- struktury JSON,
- odczytu JSON,
- mapowania,
- zapisu do PostgreSQL.

Nie należy ręcznie usuwać stagingu przed ustaleniem przyczyny.

## 10. Etap 3 — diagnostyka

Etap 3 pobiera partie ze stagingu i synchronizuje `product.template`.

Mechanizm równoległej rezerwacji wykorzystuje:

```sql
FOR UPDATE SKIP LOCKED
```

Błędy pojedynczych rekordów są obsługiwane niezależnie w ramach savepointów.

Logi:

```bash
grep '\[APDS\]' /var/log/odoo/odoo-server.log | tail -300
```

## 11. SerializationFailure

Przy rezerwacji partii prototyp obsługuje `SerializationFailure` przez:

1. rollback,
2. backoff,
3. jitter,
4. ponowienie.

Wystąpienie pojedynczego komunikatu `SerializationFailure` nie oznacza automatycznie niepowodzenia przebiegu.

Sprawdzenie:

```bash
grep 'SerializationFailure' /var/log/odoo/odoo-server.log | tail -100
```

Jeżeli błędy występują masowo lub worker kończy się błędem, należy zachować cały odpowiedni fragment logu.

## 12. Diagnostyka workerów

```bash
ps aux | grep '[o]doo'
```

oraz:

```bash
htop
```

Podczas testu można obserwować:

```bash
free -h
df -h
uptime
```

Chwilowo wysoki load przy wielu workerach nie musi oznaczać błędu. Należy analizować go razem z czasem przetwarzania i błędami.

## 13. Diagnostyka PostgreSQL

Aktywne transakcje:

```sql
SELECT pid,
       state,
       wait_event_type,
       wait_event,
       now() - xact_start AS transaction_age,
       left(query, 300) AS query
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY xact_start NULLS LAST;
```

Oczekujące blokady:

```sql
SELECT *
FROM pg_locks
WHERE NOT granted;
```

## 14. Worker zakończony błędem

Nie należy od razu:

- usuwać `communication.log`,
- usuwać stagingu,
- zmieniać ręcznie stanów stagingu SQL-em,
- uruchamiać kolejnego pełnego importu.

Najpierw należy ustalić:

```text
ID communication.log
apds_stage
apds_operation
apds_result
state
```

oraz zebrać log:

```bash
grep '\[APDS\]' /var/log/odoo/odoo-server.log | tail -300
```

## 15. Przebieg zatrzymany na Etapie 3

Sprawdzić aktywne procesy Odoo i PostgreSQL.

Jeżeli istnieją aktywne transakcje:

```sql
SELECT pid,
       state,
       now() - xact_start AS age,
       wait_event_type,
       wait_event,
       left(query, 300)
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY xact_start NULLS LAST;
```

Przed restartem należy zachować wynik diagnostyki.

## 16. Ponowienie

Przed ponowieniem należy sprawdzić stan istniejącego `communication.log`.

Nie należy tworzyć nowego przebiegu tylko dlatego, że pojedynczy worker zakończył się błędem.

Najpierw należy ustalić, czy istniejący przebieg może być kontynuowany.

## 17. Poprawne zakończenie

Po poprawnej finalizacji:

- Etap 3 jest zakończony,
- przebieg otrzymuje końcowy stan APDS,
- pojawia się komunikat finalizacyjny,
- staging dla danego przebiegu jest czyszczony.

Po wyczyszczeniu stagingu nie należy próbować weryfikować wyniku przez `apds_staging_line`.

Weryfikację należy wykonać na podstawie `communication.log` oraz danych `product.template`.

## 18. Scenariusze testowe

### A — pełny przebieg

Sprawdzić:

- pobranie,
- przygotowanie,
- staging,
- przetwarzanie,
- finalizację.

### B — istniejące produkty

Zweryfikować aktualizację produktów już istniejących w Odoo.

### C — nowe produkty

Zweryfikować utworzenie produktów nieobecnych wcześniej w Odoo.

### D — rekordy pomijane

Zweryfikować rekordy, które zgodnie z regułami prototypu nie powinny zostać zastosowane.

### E — równoległość

Zapisać:

- liczbę workerów,
- batch size,
- czas Etapu 3,
- ewentualne `SerializationFailure`,
- końcowy rezultat.

### F — ponowienie

Jeżeli możliwe jest kontrolowane przerwanie procesu, sprawdzić zachowanie po ponownym uruchomieniu.

## 19. Dane do zgłoszenia problemu

W przypadku problemu należy przekazać:

1. ID `communication.log`,
2. datę i godzinę,
3. etap i operację APDS,
4. opis oczekiwanego zachowania,
5. opis rzeczywistego zachowania,
6. fragment logu Odoo,
7. wynik `free -h`,
8. wynik `df -h`,
9. wynik `uptime`,
10. wynik diagnostyki `pg_stat_activity`,
11. informację o liczbie workerów i batch size.

Nie należy przekazywać haseł, kluczy prywatnych ani innych danych uwierzytelniających.

## 20. Ograniczenia prototypu

Prototyp służy do walidacji procesu APDS na danych klienta.

W szczególności parametry:

- liczba workerów,
- wielkość batcha,
- wydajność,
- zachowanie przy awariach,

powinny zostać potwierdzone testami przed wykorzystaniem rozwiązania produkcyjnego.

Elementy procesu, które pozostają poza zakresem aktualnego prototypu, należy traktować zgodnie z aktualną specyfikacją projektu, a nie jako funkcje gwarantowane przez prototyp.


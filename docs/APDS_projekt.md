# ALIAS Product Data Synchronization (APDS)

## Projekt

**Nazwa procesu:** ALIAS Product Data Synchronization (APDS)
**Nazwa modułu Odoo:** `hfb_apds`
**Platforma:** Odoo 19 Community Edition
**Baza integracyjna:** XET Base

Dokument jest roboczą osią projektu. Ma być rozwijany w trakcie projektowania integracji i służyć jako materiał do uzgodnień, decyzji oraz późniejszej implementacji.

---

## 1. Cel projektu

Celem APDS jest integracja danych produktów dostarczanych przez klienta ze środowiskiem Odoo.

Źródłem danych jest zewnętrzny plik JSON. Dane te są traktowane jako **master data**.

APDS ma zapewnić kontrolowaną synchronizację danych produktów pomiędzy źródłem klienta a Odoo, z uwzględnieniem:
- pełnego pliku danych,
- pliku różnicowego,
- automatycznego i ręcznego uruchamiania procesu,
- kontroli poprawności danych przed ich zastosowaniem,
- bezpiecznego przetwarzania dużego zbioru danych,
- możliwości ponowienia przetwarzania,
- rejestracji przebiegu i wyniku procesu.

Na obecnym etapie celem projektu jest **zaprojektowanie procesu biznesowego**. Implementacja nie jest jeszcze przedmiotem projektu.

---

## 2. Kontekst XET Base

APDS zostanie nałożony na moduł **XET Base**.

XET Base udostępnia mechanizm niezależnej obsługi procesów providerów. Obecnie istnieją w nim dwa niezależne providery, w tym **Local Dir**.

Dla ALIAS zostanie zaprojektowana indywidualna integracja klienta jako kolejny provider, analogicznie do rozwiązania Local Dir.

Podział odpowiedzialności:

**Provider**
- obsługuje komunikację ze źródłem,
- pobiera dane,
- realizuje techniczne operacje związane z kanałem komunikacyjnym.

**APDS**
- realizuje proces biznesowy synchronizacji,
- interpretuje dane,
- wykonuje kontrolę danych,
- synchronizuje dane produktów w Odoo.

Provider nie powinien zawierać logiki biznesowej produktów.

### 2.1. Mechanizm XET Base — potwierdzone przez analizę kodu (`hfb_xmlmap_exporter`, gałąź 19.0)

`hfb_apds` będzie zależał od modułu `hfb_xmlmap_exporter` i rozbuduje go **analogicznie do sposobu, w jaki zrobiono to dla obsługi KSeF — jako odrębny moduł domenowy**, a nie zmianę mechanizmu bazowego.

Potwierdzone fakty z kodu:

- Bazowy model `communication.provider` udostępnia mechanizm polimorficzny: `provider_type` (Selection rozszerzane przez `selection_add`), `provider_model` (obliczany model pluginu), `provider_config_id` (konkretna konfiguracja) oraz kontrakt metod do zaimplementowania przez każdy provider: `authenticate()`, `fetch_documents()`, `send_document()`, `get_status()`. ALIAS wpina się tu jako `communication.provider.alias`, analogicznie do `communication.provider.localdir`.
- **`communication.log` pozostaje centralną osią przetwarzania** — jeden rekord `communication.log` odpowiada jednemu przebiegowi/operacji. Ma wbudowany audyt (`mail.thread`, `mail.activity.mixin`, `tracking=True`), co realizuje UC-11 praktycznie bez dodatkowej pracy.
- Bazowy `state` na `communication.log` (`draft → generated → validated → queued → sent → received → superseded → error`) opisuje ogólny cykl transportowy i **nie jest rozciągany** przez moduły domenowe pod ich własną logikę.
- Moduł KSeF (wzorzec do naśladowania) nie modyfikuje bazowego `state` pod swój proces. Zamiast tego dokłada na `communication.log` (przez `_inherit`) **własną, równoległą warstwę pól domenowych**, m.in.:
  - `ksef_operation` — bieżący krok w sekwencji specyficznej dla KSeF (np. `auth → open_session → send_invoice → check_status → download_upo → close_session`, względnie `import_list → import_invoice → completed/failed`),
  - `ksef_next_operation` — zaplanowany następny krok,
  - `ksef_status` (`pending / in_progress / success / failed / waiting_delay`) — status wykonania bieżącego kroku, odrębny od generycznych `status` i `state`,
  - `is_processing` + `processing_lock_until` / `processing_pid` — blokada współbieżności,
  - `parent_id` / `child_ids` (+ `ksef_source_list_id`, `ksef_created_jobs`, `ksef_discovered_count`) — jeden log nadrzędny (np. lista faktur) może być rodzicem wielu logów potomnych.

**Ustalony kierunek dla APDS:** trzy stany procesu głównego — (1) pobranie pliku, (2) przygotowanie do przetwarzania, (3) przetwarzanie (obejmujące też ocenę wyniku zbiorczego, podsumowanie i zamknięcie logu) — będą realizowane analogicznie do `ksef_operation` / `ksef_status`, tzn. jako własna para pól domenowych dodana do `communication.log` przez `hfb_apds` (roboczo: coś w rodzaju `apds_operation` + `apds_status`), **obok** bazowego `state`, a nie w jego miejsce. Szczegóły — patrz sekcja 6.

Wzorzec `parent_id` / `child_ids` jest potencjalnie przydatny inaczej niż w KSeF: przy ~2,12 mln rekordów nie zakładamy jednego rekordu potomnego na produkt, ale mechanizm ten może posłużyć do powiązania logu wyjątków (np. duplikaty EAN, odrzucone rekordy) z logiem głównym przebiegu — do doprecyzowania na etapie projektowania implementacji.

**Wznowienie i retry — ustalony kierunek (patrz 6.5):** wzorzec KSeF pokazuje, że przy błędzie log jest zapisywany z zapamiętanym miejscem zatrzymania (u KSeF: `ksef_next_operation`), a wznowienie kontynuuje od tego miejsca, nie od początku sekwencji. APDS ma działać analogicznie. Mechanizm liczby prób i opóźnienia między nimi ma na start reużywać istniejące pola **na poziomie providera** (`auto_retry`, `retry_attempts`, `retry_delay`, `notification_users` z `communication.provider`), bez osobnego licznika prób per etap.

Nazewnictwo konkretnych pól, ich pełny zestaw wartości oraz szczegóły relacji rodzic–dziecko pozostają decyzją etapu implementacji modułu `hfb_apds` (sekcja 10) — powyższe ustala jedynie **wzorzec architektoniczny**, z którym proces biznesowy (sekcja 6) musi być spójny.

---

## 3. Źródło danych

Źródłem danych jest klient.

### Kanał i dostęp

Dane są udostępniane poprzez:
- **SFTP** (nie HTTPS — ustalone jednoznacznie),
- dedykowane **subkonto SFTP** dla integracji ALIAS,
- plik skompresowany **7zip**.

### Plik źródłowy — jeden plik, zmienna zawartość [ZMIANA względem wcześniejszej wersji]

**Ustalone z klientem:** to **jeden plik** — ta sama nazwa, to samo miejsce pobrania za każdym razem — którego **zawartość** zmienia się w zależności od dnia:
- **codziennie** — zawartość różnicowa (dane, które faktycznie uległy zmianie),
- **raz w tygodniu, w weekend** — zawartość pełna (kompletna baza klienta, do pełnej synchronizacji z Odoo).

To zastępuje wcześniejsze założenie o dwóch odrębnych plikach/kanałach (pełnym i różnicowym) — było otwartym pytaniem, teraz rozstrzygnięte.

Znane parametry (dotyczą wariantu pełnego, zaobserwowane na rzeczywistym pliku):
- plik powstaje w nocy, około godziny 4:00,
- wariant pełny: rozmiar pliku JSON po rozpakowaniu ~2,305 GB (2 305 104 590 B), ~2,12 mln rekordów,
- wariant różnicowy: mniejszy, dokładny rozmiar nieznany — zależny od faktycznej liczby zmian danego dnia.

Wersję pliku identyfikujemy na podstawie metadanych pliku, w szczególności **daty utworzenia**.

Przed pobraniem sprawdzamy dostępność pliku. Możliwe jest ponowne pobranie pliku.

**Nowa, nierozstrzygnięta kwestia wynikająca z tego ustalenia — patrz [9.9](#99-rozpoznawanie-rodzaju-zawartości-pliku).** Skoro to jeden plik o zmiennej zawartości, system musi **rozpoznać z samej zawartości**, czy dany dzień przyniósł wariant pełny czy różnicowy — nie może tego wywnioskować z tego, który plik pobrał, bo to zawsze ten sam plik.

Szczegółowa semantyka rekordów w wariancie różnicowym pozostaje otwarta — patrz [9.2](#92-semantyka-pliku-różnicowego).

---

## 4. Główne założenia procesu

### 4.1. JSON jest master data

Dane źródłowe klienta stanowią źródło prawdy dla zakresu danych objętych synchronizacją. Potwierdzone wprost na spotkaniu 28.08.26.

### 4.2. Pobrany plik nie jest automatycznie zaakceptowanym stanem

Pobranie pliku i zastosowanie jego danych w Odoo są odrębnymi etapami.

Plik musi przejść odpowiednie kontrole, zanim stanie się nowym stanem referencyjnym (patrz [6.4](#64-kryteria-akceptacji-jako-nowego-stanu-referencyjnego)).

### 4.3. Błędne dane nie mogą zastąpić poprawnego stanu

Jeżeli plik jest niepełny, uszkodzony lub nie spełnia ustalonych warunków, nie powinien zostać zastosowany jako nowy stan danych.

Poprzedni poprawny stan pozostaje obowiązujący — potwierdzone jednoznacznie ("tak") na spotkaniu 28.08.26.

### 4.4. Proces musi być powtarzalny

Powinna istnieć możliwość ponownego przetworzenia konkretnej wersji danych, w tym ponowienia po awarii, bez oczekiwania na kolejny plik.

### 4.5. Proces musi być audytowalny

Zgodnie z dobrymi praktykami zespołu: dedykowane menu logowania aktywności i błędów procesu.

Powinno być możliwe ustalenie:
- kiedy wykonano proces,
- jaki plik został użyty,
- jaki był wynik,
- czy wystąpiły błędy,
- jakie działania zostały wykonane.

### 4.6. Wydajność jako założenie projektowe

Skala danych (2,2 GB / ~2,12 mln rekordów) oznacza, że **codzienne pełne przetwarzanie przez standardowy ORM Odoo (rekord po rekordzie) jest podejściem niewłaściwym** — ustalone wprost na spotkaniu 28.08.26.

Wnioski projektowe:
- plik różnicowy ma być głównym mechanizmem ograniczającym ruch do Odoo wyłącznie do rzeczywistej delty,
- pełny plik ma pełnić rolę tygodniowej synchronizacji oraz **mechanizmu kontrolnego** wobec stanu wynikającego z synchronizacji różnicowych, a nie codziennego trybu podstawowego,
- sposób technicznego przetwarzania (batchowanie, operacje masowe, ominięcie części mechanizmów ORM przy dużych wolumenach) jest kwestią do rozstrzygnięcia na etapie projektowania implementacji (`hfb_apds`), zgodnie z zasadą z sekcji 10 — na obecnym etapie odnotowujemy to jako wymaganie niefunkcjonalne, nie projektujemy jeszcze rozwiązania technicznego.

---

## 5. Rodzaje synchronizacji

### 5.1. Synchronizacja pełna

Pełny plik przedstawia kompletny stan danych źródłowych.

Planowane wykorzystanie:
- raz w tygodniu,
- jako pełna synchronizacja,
- również jako mechanizm kontrolny poprawności stanu wynikającego z synchronizacji różnicowych.

### 5.2. Synchronizacja różnicowa

Wariant różnicowy zawartości pliku (patrz sekcja 3) zawiera dane dotyczące zmian.

Planowane wykorzystanie:
- codziennie,
- w celu ograniczenia liczby rekordów podlegających przetwarzaniu.

Szczegółowa definicja semantyki wariantu różnicowego wymaga jeszcze potwierdzenia z klientem — patrz [9.2](#92-semantyka-pliku-różnicowego).

---

## 6. Przebieg procesu

### 6.0. Jeden proces, rozpoznawany rodzaj zawartości [ZMIANA względem wcześniejszej wersji]

**Ta sekcja zastępuje wcześniejszą treść** ("dwa cykle główne", pełny i różnicowy jako odrębne cykle). Ustalone z klientem (patrz sekcja 3): to **jeden plik, jeden proces, jeden harmonogram** — nie dwa niezależne cykle. To, czy dany dzień przyniósł zawartość pełną czy różnicową, jest właściwością **konkretnego przebiegu** (rozpoznawaną z zawartości pliku — patrz [9.9](#99-rozpoznawanie-rodzaju-zawartości-pliku)), nie wyborem między dwoma z góry zaprojektowanymi ścieżkami.

Oba warianty zawartości przechodzą przez te same trzy stany (6.1) — różni je zakres danych wejściowych i wynik rozpoznania rodzaju, nie odrębny harmonogram czy odrębna konfiguracja.

**Pole "rodzaj aktualizacji" (`pełna`/`różnicowa`) nie jest polem konfiguracyjnym providera** (jak wcześniej zakładano) — to wynik rozpoznania, zapisywany **per przebieg**, na `communication.log`, nie ustawiany raz na stałe (patrz też `APDS_wczytanie_JSON_i_workery.md`, sekcja 12a).

Ustalenie to zamyka też wcześniejszą kwestię [9.8](#98-nakładające-się-w-czasie-przebiegi-tego-samego-providera) — skoro jest jeden trigger i jeden proces, nie dwa niezależne harmonogramy, nakładanie się przebiegów tego samego providera jest strukturalnie wykluczone, nie tylko założone.

### 6.1. Trzy stany procesu głównego

Proces APDS należy rozumieć jako ciąg trzech stanów `communication.log` (patrz 2.1), realizowanych sekwencyjnie — każdy kolejny stan inicjowany jest dopiero po poprawnym zakończeniu poprzedniego:

**(1) pobranie pliku → (2) przygotowanie do przetwarzania → (3) przetwarzanie → [Zaakceptowane / Wymaga interwencji ręcznej]**

**Stan 1 — Pobranie pliku**

Do realizacji potrzebne jest określenie (konfiguracja providera):
- dane dostępowe do usługi zdalnej (SFTP, subkonto dedykowane),
- ścieżka do umieszczenia pliku w obrębie serwera.

Etap kończy się pobraniem paczki na serwer — **bez rozpakowywania**. Odpowiada operacji `fetch_documents()` na poziomie `communication.provider` (UC-03).

Niepowodzenie (plik niedostępny, niepełne pobranie) kończy proces na tym etapie, bez inicjowania stanu 2 — patrz 6.2.

**Stan 2 — Przygotowanie do przetwarzania**

Obejmuje: rozpakowanie pliku (jeśli to paczka 7zip), "wstrzyknięcie" zawartości do tabeli tymczasowej, a następnie **usuwanie plików fizycznych** (paczka, rozpakowany plik JSON — sprzątanie po sobie, bez trzymania kopii na dysku po załadowaniu do bazy).

Ładowanie ustrukturyzowanych danych do tabeli tymczasowej z natury wymusza kontrolę techniczną (UC-04 — poprawność JSON, zgodność ze strukturą). Kontrola biznesowa (UC-05 — % zerowania stanów) wykonywana jest na danych znajdujących się już w tabeli tymczasowej, zanim cokolwiek trafi do właściwych rekordów Odoo.

Niepowodzenie na tym etapie kończy proces bez inicjowania stanu 3, a rekordy produktów w Odoo pozostają nietknięte — dane leżały wyłącznie w tabeli tymczasowej. Patrz 6.2 oraz [9.7](#97-transakcyjność-i-wycofywalność-częściowego-przetworzenia).

**Stan 3 — Przetwarzanie**

Obejmuje całość dalszego przebiegu, bez wydzielania osobnych etapów: właściwą synchronizację danych produktów w Odoo na podstawie danych z tabeli tymczasowej (UC-06, UC-07), **ocenę wyniku zbiorczego** (czy wystąpił błąd krytyczny), **podsumowanie** oraz **zamknięcie `communication.log`** (UC-08, UC-11).

Zakończenie stanu 3 prowadzi do jednego z dwóch stanów końcowych:
- **Zaakceptowane — nowy stan referencyjny**, gdy przetwarzanie zakończyło się bez błędu krytycznego (UC-08),
- **Wymaga interwencji ręcznej**, gdy wystąpił błąd krytyczny lub kontrola zakończyła się negatywnie (odpowiednik UC-09 — odrzucenie synchronizacji); poprzedni poprawny stan pozostaje nienaruszony.

### 6.1a. Powiązanie z otwartą kwestią 9.7

Podejście z tabelą tymczasową w stanie 2 częściowo adresuje pytanie z sekcji 9.7: dopóki dane są w obszarze roboczym (staging), niepowodzenie nie dotyka rekordów produktów w Odoo — "poprzedni stan nienaruszony" jest wtedy dosłownie prawdziwy dla stanów 1–2. Nadal otwarte pozostaje zachowanie w przypadku niepowodzenia **w trakcie stanu 3** (przetwarzanie), gdy dane są już zapisywane do właściwych rekordów Odoo.

### 6.2. Obsługa błędów na poziomie całego pliku

Ustalone na spotkaniu 28.08.26 — w każdym z poniższych przypadków: **przetwarzanie jest pomijane, próba jest logowana**, a poprzedni poprawny stan pozostaje nienaruszony:

| Sytuacja | Reakcja procesu |
|---|---|
| Niepełne pobranie pliku | Pomijamy przetwarzanie, log próby |
| Uszkodzony / niepoprawny JSON | Pomijamy przetwarzanie, log próby |
| Zmiana struktury eksportu względem znanej struktury | Struktura jest z założenia stała; jeśli mimo to ulegnie zmianie — pomijamy przetwarzanie |
| Brak pliku u źródła | Pomijamy przetwarzanie, log próby |

### 6.2a. Obsługa błędów wg etapu i możliwość wznowienia

Poza błędami danych (tabela w 6.2), proces musi obsłużyć błędy infrastrukturalne: problemy z komunikacją, z plikiem, z miejscem na dyskach. Rozkładają się one na etapy następująco:

| Stan | Komunikacja | Plik | Dysk / miejsce |
|---|---|---|---|
| 1. Pobranie | SFTP niedostępny, timeout, zerwane połączenie w trakcie transferu | plik niedostępny u źródła, niepełne pobranie | brak miejsca na serwerze na zapis paczki |
| 2. Przygotowanie do przetwarzania | — | uszkodzona paczka 7zip, niepoprawny JSON | brak miejsca na rozpakowany plik, brak miejsca w bazie na tabelę tymczasową |
| 3. Przetwarzanie (w tym ocena wyniku, podsumowanie, zamknięcie logu) | problemy z samą bazą Odoo (timeout, blokady) | — (dane już zwalidowane w stanie 2) | miejsce w bazie na docelowe rekordy |

**Wznowienie po błędzie — ustalone:** wznowienie jest **kontynuacją od etapu, na którym proces się zatrzymał**, a nie restartem całego cyklu od etapu 1. Analogicznie do wzorca KSeF: log zapamiętuje, w którym miejscu proces stanął, a kolejna próba kontynuuje stamtąd.

**Mechanizm retry — ustalone:** na poziomie providera, reużycie istniejących pól `communication.provider` (`auto_retry`, `retry_attempts`, `retry_delay`, `notification_users`) — bez osobnego licznika prób per etap na tym etapie projektu.

**Błędy przejściowe vs trwałe — ustalone:** nie jest to osobna kategoryzacja błędów, tylko efekt działania mechanizmu retry — błąd mieszczący się w dozwolonej liczbie prób jest automatycznie ponawiany (efektywnie: przejściowy); błąd, który wyczerpał limit prób, wymaga ręcznej interwencji (efektywnie: trwały).

### 6.3. Obsługa błędów na poziomie pojedynczego produktu

Przykład ustalony: **duplikat EAN** → rekord jest logowany i pomijany (nie tworzymy/nie aktualizujemy produktu), a duplikaty są zbierane na osobnej liście wyjątków do przeglądu.

Dodatkowa reguła z mapowania danych: jeśli `stock.total == 0`, rekord jest pomijany — pozwala to również wykluczyć część duplikatów.

Pełna klasyfikacja błędów na poziomie rekordu (krytyczny dla całego procesu / lokalny dla produktu / ostrzeżenie) pozostaje do dopracowania — patrz [9.3](#93-granica-błędu).

### 6.4. Kryteria akceptacji jako nowego stanu referencyjnego

Ustalone na spotkaniu 28.08.26:

**Import uznajemy za poprawny, gdy:**
- brak błędów krytycznych,
- plik został przetworzony do końca.

**Wynik importu staje się nowym stanem referencyjnym, gdy dodatkowo:**
- plik jest kompletny (nie jest pusty, nie jest częściowy),
- kontrola ALIAS nie wykazała podejrzanie dużego procentu wyzerowanych stanów magazynowych (szczegóły progu — patrz [9.4](#94-kontrola-stanów)).

**Wznowienie po awarii:** błędy krytyczne są logowane, a przetwarzanie danego pliku jest pomijane (nie wpływa na stan referencyjny) — można je ponowić zgodnie z UC-10.

---

## 7. Przypadki użycia

### UC-01 — Automatyczne uruchomienie synchronizacji

Proces uruchamiany jest automatycznie zgodnie z harmonogramem.

Docelowo:
- codziennie — synchronizacja różnicowa,
- raz w tygodniu — synchronizacja pełna.

Przed pobraniem pliku sprawdzana jest jego dostępność u źródła.

### UC-02 — Ręczne uruchomienie synchronizacji

Użytkownik (lub inny mechanizm) może uruchomić proces niezależnie od harmonogramu.

Ustalone: uruchomienie ręczne **nie musi mieć formy przycisku w UI** — dopuszczalny jest cron, link lub trigger przez API. Konkretna forma zostanie doprecyzowana na etapie projektowania implementacji.

### UC-03 — Pobranie danych

Provider ALIAS pobiera paczkę z udostępnionego źródła (SFTP, subkonto dedykowane) i umieszcza ją pod skonfigurowaną ścieżką na serwerze (etap 1 — sekcja 6.1). Etap kończy się na samym pobraniu — bez rozpakowywania.

Proces powinien umożliwiać pracę z konkretną wersją pliku oraz jej ponowne pobranie.

### UC-04 — Kontrola pobranego pliku

Realizowana w ramach etapu 2 (sekcja 6.1), przy rozpakowywaniu i wstrzykiwaniu danych do tabeli tymczasowej. Przed zastosowaniem danych należy zweryfikować co najmniej:
- poprawność pobrania,
- kompletność pliku,
- możliwość rozpakowania (7zip),
- poprawność JSON,
- zgodność ze znaną strukturą.

W przypadku niepowodzenia którejkolwiek z kontroli: przetwarzanie jest pomijane, próba jest logowana (patrz 6.2), a rekordy produktów w Odoo pozostają nietknięte (dane nie wyszły poza tabelę tymczasową).

Zakres kontroli będzie rozszerzany w trakcie projektu.

### UC-05 — Kontrola biznesowa danych

Dane powinny zostać poddane kontroli przed wykonaniem synchronizacji.

Jednym z ustalonych wymagań jest kontrola **podejrzanie dużego procentu wyzerowanych stanów magazynowych** — wykonywana przez ALIAS jako część oceny, czy wynik może stać się nowym stanem referencyjnym.

Jeżeli wynik kontroli wskazuje na prawdopodobny błąd danych źródłowych, synchronizacja nie powinna zostać zaakceptowana.

Próg oraz szczegółowa reguła kontroli pozostają do ustalenia — patrz [9.4](#94-kontrola-stanów).

### UC-06 — Synchronizacja produktu

Dane produktu są wyszukiwane i synchronizowane w Odoo.

Klucz identyfikacyjny produktu (potwierdzone na spotkaniu 28.08.26: *"Prefiks plus indeks jako unikalny SKU produktu"*):

`prefix + " " + index`

Wartość ta odpowiada polu:

`product.default_code`

Przykład:

`A-P BOS 1987947837`

Struktura danych jest płaska — mapowanie odbywa się **1:1 na `product.template`**, bez modelowania wariantów (`product.product`).

### UC-07 — Obsługa nieprawidłowych danych produktu

Proces musi obsługiwać przypadki, w których pojedyncze dane produktu są nieprawidłowe lub nie mogą zostać zastosowane.

Przykłady ustalone:
- **duplikat EAN** → rekord logowany i pomijany, zbierana lista wyjątków (nie tworzymy produktu na podstawie zduplikowanego EAN),
- **`stock.total == 0`** → rekord pomijany (dodatkowo ogranicza to liczbę duplikatów).

Dla pozostałych przypadków należy ustalić, które błędy:
- pomijają pojedynczy rekord,
- generują ostrzeżenie,
- zatrzymują cały proces.

Patrz też [9.3](#93-granica-błędu).

### UC-08 — Zakończenie i zaakceptowanie synchronizacji

Po poprawnym przetworzeniu danych i spełnieniu kryteriów z sekcji [6.4](#64-kryteria-akceptacji-jako-nowego-stanu-referencyjnego) proces kończy się sukcesem — stan **Zaakceptowane — nowy stan referencyjny** (sekcja 6.1).

Wersja danych zostaje uznana za poprawnie przetworzoną i staje się nowym stanem referencyjnym.

### UC-09 — Odrzucenie synchronizacji

Jeżeli wystąpi błąd krytyczny lub kontrola danych (w tym kontrola % zerowania stanów) zakończy się negatywnie — stan **Wymaga interwencji ręcznej** (sekcja 6.1):
- synchronizacja nie zostaje zaakceptowana,
- wynik zostaje zarejestrowany,
- poprzedni poprawny stan pozostaje nienaruszony.

### UC-10 — Ponowienie procesu

Możliwe jest ponowienie przetwarzania konkretnej wersji danych, w tym po awarii.

Powinno to pozwalać na ponowne wykonanie procesu bez oczekiwania na kolejny plik. Potwierdzone na spotkaniu 28.08.26 — "tak" jako wymaganie.

**Ustalone (patrz 6.2a):** wznowienie jest kontynuacją od stanu (1–3, sekcja 6.1), na którym proces się zatrzymał — nie restartem całego cyklu od stanu 1. Liczba prób i opóźnienie między nimi sterowane są mechanizmem retry na poziomie providera (`auto_retry` / `retry_attempts` / `retry_delay`).

### UC-11 — Audyt procesu

Historia procesu powinna pozwalać odtworzyć jego przebieg, w formie dedykowanego menu logowania aktywności i błędów (zgodnie z dobrymi praktykami zespołu).

Zakres informacji do ustalenia obejmuje m.in.:
- moment rozpoczęcia,
- źródło uruchomienia (automatyczne / ręczne, i jakim mechanizmem),
- identyfikację pliku (wersja, data utworzenia),
- rodzaj synchronizacji (pełna / różnicowa),
- wynik (zaakceptowany / odrzucony),
- błędy i ostrzeżenia (w tym lista wyjątków, np. duplikaty EAN),
- moment zakończenia.

---

## 8. Mapowanie danych produktu

Mapowanie oparte na pliku `Mapowanie pól z pliku JSON.ods`. Struktura źródłowa jest płaska, mapowana 1:1 na `product.template`.

| Pole JSON | Pole Odoo | Komentarz |
|---|---|---|
| `prefix` + `index` | `default_code` | `prefix<spacja>index` = unikalny SKU (potwierdzone) |
| `name` | `name` | |
| `description` | `description` | Pomijane na obecnym etapie |
| `info` | — | Nieustalone |
| `producer` | nowe pole (char) | |
| `ean` | `barcode` | Jeśli duplikat: logujemy, pomijamy tworzenie, zbieramy listę wyjątków |
| `uom` | `uom_id` | |
| `pkwiu` | nowe pole (char) | |
| `cn` | nowe pole (char) / many2one — decyzja otwarta | Pole commodity code / HS code; dostępne standardowo tylko w Odoo Enterprise (`account_intrastat`) — **wymaga decyzji o podejściu w CE** |
| `gtu` | `ksef.gtu` (many2one) | Model już istnieje w systemie |
| `tax` | `taxes_id` (many2many) | Wartość domyślna: 23% |
| `price.buy.netto` | `standard_price` / `seller_ids.price` (`variant_seller_ids.price`) | Netto zakupu |
| `price.sell.netto` | `list_price` | Netto sprzedaży; Odoo liczy globalnie w cenach netto |
| `price.srp` | — | Nieustalone |
| `price.profit.netto` | — | Nieustalone |
| `stock.local` | stan lokalny (`qty_available`?) | Tylko import początkowy — dalsze zasady aktualizacji do ustalenia |
| `stock.foreign` | — | Nie występuje w praktyce / nieustalone |
| `stock.supplier` | nowe pole (float) | |
| `stock.total` | nowe pole (float) | **Jeśli `total == 0` → pomijamy rekord** (wyklucza część duplikatów) |
| `supplier` | `seller_ids.partner_id.name` / `variant_seller_ids.partner_id.name` | |
| `supplier_id` | `seller_ids` / `variant_seller_ids` | |
| `oe` | — | Nieustalone |
| `discount_group` | nowe pole (char) / many2one — decyzja otwarta | |
| `category.id` / `category.name` | `categ_id` / `categ_id.name` | Kategorię **tworzymy, jeśli nie istnieje** |
| `compatibility` | — | Nieustalone |
| `images` | `image_1920` | |
| `flags.*` (urgent, watch, quality, new, recom, promo, sale, used, serviced, open, use, allegro_product) | tag produktu | Nazwę flagi importujemy jako tag na produkcie, **jeśli wartość = `true`** |
| `conn.repl` / `conn.link` | — | Nieustalone |
| `attributes` | — | Niezmapowane |

Powyższe mapowanie jest materiałem roboczym i będzie rozwijane wraz z decyzjami z sekcji 9.

---

## 9. Kwestie wymagające decyzji

### 9.1. Brak produktu w pełnym pliku

Nie ustalono jeszcze, czy brak produktu w pełnym pliku oznacza:
- archiwizację produktu,
- usunięcie powiązania,
- brak działania,
- inną operację.

Status po spotkaniu 28.08.26: **nadal nieokreślone**.

### 9.2. Semantyka pliku różnicowego

Do potwierdzenia pozostaje dokładne znaczenie:
- rekordu dodanego,
- rekordu zmienionego,
- rekordu usuniętego,
- braku rekordu,
- jak odróżnić "produkt usunięty z pliku" od chwilowego problemu z eksportem po stronie klienta.

Wiadomo już: plik różnicowy będzie miał tę samą strukturę co plik pełny i ma być dostępny po weekendzie (data do potwierdzenia — patrz sekcja 3). Szczegóły semantyki — **nadal nieokreślone**, wymagają potwierdzenia z klientem.

### 9.3. Granica błędu

Częściowo rozstrzygnięte (patrz 6.2, 6.3): błędy na poziomie całego pliku (niepełne pobranie, uszkodzony JSON, brak pliku, zmiana struktury) → pomijamy cały plik; duplikat EAN i `stock.total == 0` → pomijamy pojedynczy rekord.

Nadal do ustalenia: pełna klasyfikacja pozostałych możliwych błędów na poziomie pojedynczego produktu — które są:
- krytyczne dla całego procesu,
- lokalne dla pojedynczego produktu,
- jedynie ostrzeżeniami.

### 9.4. Kontrola stanów

Potwierdzone: kontrolę wykonuje ALIAS jako warunek uznania wyniku za nowy stan referencyjny (patrz 6.4).

Do ustalenia pozostaje (dosłownie: *"szczegóły do ustalenia na późniejszym etapie"*):
- sposób obliczenia procentu wyzerowanych stanów,
- próg alarmowy,
- zakres produktów objętych kontrolą,
- zachowanie po przekroczeniu progu (poza samym odrzuceniem synchronizacji — np. powiadomienia).

### 9.5. Zasady tworzenia i aktualizacji produktów

Częściowo rozstrzygnięte: JSON jest master data (4.1); kategorię tworzymy, jeśli nie istnieje (sekcja 8).

Nadal do doprecyzowania:
- kiedy dokładnie tworzymy nowy produkt, a kiedy aktualizujemy istniejący (poza dopasowaniem po `default_code`),
- które pola są własnością źródła (nadpisywane przy każdej synchronizacji),
- które pola mogą być modyfikowane lokalnie w Odoo i nie powinny być nadpisywane,
- zasady dla pól oznaczonych w mapowaniu jako "nieustalone" (`info`, `oe`, `compatibility`, `price.srp`, `price.profit.netto`, `conn.*`, `attributes`, `stock.foreign`).

### 9.6. Dane niepochodzące bezpośrednio z JSON

W dużej mierze rozstrzygnięte mapowaniem z sekcji 8:
- podatki → `taxes_id`, domyślnie 23%,
- kategoria → `categ_id`, tworzona jeśli nie istnieje,
- GTU → istniejący model `ksef.gtu`,
- flagi → tagi produktu przy wartości `true`,
- dostawca → `seller_ids` / `variant_seller_ids`.

Nadal otwarte:
- podejście do `cn` (commodity code / HS code) w kontekście Odoo **Community Edition** (funkcja natywnie dostępna tylko w Enterprise),
- charakter pola `discount_group` (nowe pole czy many2one) i jego dalsza logika biznesowa,
- jednostki miary (`uom`) — czy mapowanie na istniejące `uom_id` jest zawsze jednoznaczne, czy wymaga słownika/mapowania wartości.

### 9.7. Transakcyjność i wycofywalność częściowego przetworzenia

Założenie 4.3 mówi, że błędne dane nie mogą zastąpić poprawnego stanu, a poprzedni poprawny stan pozostaje nienaruszony przy odrzuceniu synchronizacji. Przy skali ~2,12 mln rekordów rodziło to pytanie o transakcyjność.

**Częściowo rozstrzygnięte (sekcja 6.1, 6.1a):** wprowadzenie tabeli tymczasowej w etapie 2 sprawia, że dla etapów 1–2 "poprzedni stan nienaruszony" jest dosłownie prawdziwe — dane leżą wyłącznie w obszarze roboczym, zanim trafią do właściwych rekordów Odoo. Wznowienie po błędzie (6.2a) jest kontynuacją od etapu zatrzymania, nie restartem.

**Nadal otwarte:**
- co się dzieje z rekordami **już zapisanymi** do Odoo, jeśli błąd krytyczny wystąpi **w trakcie etapu 3** (przetwarzanie), np. po zsynchronizowaniu 1,5 mln z 2,12 mln rekordów,
- czy wznowienie etapu 3 oznacza przetworzenie tabeli tymczasowej od nowa (ryzyko podwójnego zastosowania już zsynchronizowanych rekordów), czy kontynuację od konkretnego miejsca w tabeli tymczasowej — ta granularność nie została jeszcze ustalona,
- czy cały etap 3 ma być objęty jedną transakcją bazodanową (technicznie trudne/kosztowne przy takim wolumenie), czy dopuszczalne jest przetwarzanie w miejscu, z akceptacją że produkty w Odoo mogą być chwilowo częściowo zsynchronizowane do czasu udanego wznowienia.

Rozstrzygnięcie tych pytań wpływa bezpośrednio na projekt etapu 3 i na to, czy UC-10 (ponowienie procesu) musi też obsługiwać częściowe cofanie zmian.

### 9.8. Nakładające się w czasie przebiegi tego samego providera — ZAMKNIĘTE

Czy dopuszczamy sytuację, w której przebieg synchronizacji pełnej i przebieg synchronizacji różnicowej (ten sam provider ALIAS) wykonują się jednocześnie?

**Rozstrzygnięte przez ustalenie z klientem (patrz sekcja 3):** to jeden plik, jeden trigger, jeden proces — nie dwa niezależne harmonogramy. Nakładanie się przebiegów tego samego providera jest strukturalnie wykluczone, nie tylko założone jako mało prawdopodobne.

**Rekomendacja z poprzedniej wersji tej sekcji pozostaje aktualna niezależnie od zamknięcia tego pytania:** dodanie własnego constraintu unikalności na `product.product.default_code` + rozszerzenie mechanizmu obsługi błędów rekordu o przypadek konfliktu (patrz `APDS_wczytanie_JSON_i_workery.md`, sekcja 10.3) ma niski koszt i chroni przed innymi, niezwiązanymi z tym pytaniem scenariuszami wyścigu (np. równoległe workery w obrębie *jednego* przebiegu, jeśli pre-pass deduplikacji zawiedzie). Do rozważenia niezależnie.

### 9.9. Rozpoznawanie rodzaju zawartości pliku

Skoro plik jest zawsze ten sam (ta sama nazwa, to samo miejsce), a jego zawartość zmienia się między wariantem pełnym (weekend) a różnicowym (codziennie) — system musi **rozpoznać z zawartości**, z którym wariantem ma do czynienia, nie może tego wywnioskować z tego, który plik pobrał.

Możliwe podejścia, żadne jeszcze nie ustalone:
- **liczba rekordów w pliku** — pełny ≈ 2,12 mln, różnicowy zdecydowanie mniej; wymaga ustalenia progu odróżniającego,
- **wskaźnik/pole w samym JSON-ie** — nieznane, czy istnieje; do sprawdzenia na pierwszym rzeczywistym pliku wariantu pełnego,
- **dzień tygodnia jako wskazówka pomocnicza** — kruche rozwiązanie, wiąże logikę biznesową z kalendarzem zamiast z danymi; raczej niewskazane jako jedyne źródło rozpoznania, co najwyżej pomocnicze potwierdzenie.

Wynik rozpoznania zapisywany jest per przebieg na `communication.log` (patrz 6.0) — nie jest polem konfiguracyjnym providera.

---

## 10. Zasada projektowa

> **Najpierw projektujemy proces biznesowy APDS, następnie nakładamy go na mechanizmy XET Base, a dopiero później projektujemy implementację modułu `hfb_apds`.**

Nie należy podejmować decyzji implementacyjnych przed zamknięciem odpowiednich decyzji biznesowych. Dotyczy to w szczególności podejścia technicznego do wydajnego przetwarzania dużego wolumenu danych (patrz 4.6) — na obecnym etapie odnotowujemy to jako wymaganie, nie jako rozwiązanie.

---

## 11. Historia decyzji

### 2026-08-28 — Spotkanie robocze (komentarze do pytań Andrzeja)

Ustalono m.in.:
- kanał dostępu do danych: SFTP (subkonto dedykowane) + paczka 7zip, **nie** HTTPS,
- plik pełny powstaje w nocy, ok. godz. 4:00; możliwe ponowne pobranie; wersja identyfikowana m.in. datą utworzenia,
- `prefix + index` to potwierdzony unikalny SKU / `default_code`,
- struktura danych jest płaska, mapowanie 1:1 na `product.template`,
- JSON jest master data,
- plik różnicowy — ta sama struktura co plik pełny, planowane udostępnienie po weekendzie (termin do potwierdzenia),
- codzienne pełne przetwarzanie przez ORM Odoo jest niewłaściwe przy skali ~2,12 mln rekordów — plik różnicowy ma ograniczyć ruch do rzeczywistej delty,
- błędy plikowe (niepełne pobranie, uszkodzony JSON, brak pliku) → pomijamy przetwarzanie, logujemy próbę; poprzedni poprawny stan pozostaje nienaruszony,
- kryteria uznania importu za poprawny i za nowy stan referencyjny (patrz 6.4),
- harmonogram: sprawdzanie dostępności przed pobraniem, uruchomienie automatyczne, możliwość uruchomienia ręcznego (niekoniecznie przyciskiem — cron/link/API) oraz ponowienia dla konkretnej wersji,
- audyt: menu logowania aktywności i błędów wg dobrych praktyk zespołu.

Nadal otwarte: los produktu znikającego z pełnego pliku (9.1), pełna semantyka pliku różnicowego (9.2), szczegóły kontroli zerowania stanów (9.4).

### 2026-08-31

Ustalono:
- nazwę procesu: **ALIAS Product Data Synchronization (APDS)**,
- nazwę modułu: **`hfb_apds`**,
- platformę: **Odoo 19 CE**,
- wykorzystanie XET Base,
- budowę indywidualnego providera ALIAS na wzór Local Dir,
- rozdzielenie odpowiedzialności providera i procesu biznesowego,
- dalsze projektowanie rozpoczynamy od procesu biznesowego i przypadków użycia.

Włączono do dokumentu ustalenia ze spotkania 28.08.26 oraz mapowanie pól z pliku `Mapowanie pól z pliku JSON.ods`; rozwinięto sekcje 3, 4, 6, 7, 8 i 9 o odpowiadające im szczegóły.

Zaprojektowano szczegółowy model przebiegu procesu (sekcja 6) oparty na dwóch bramkach kontrolnych: kontroli przed synchronizacją (techniczna + biznesowa, na surowych danych pliku) oraz ocenie wyniku zbiorczego po przetworzeniu rekordów. Zidentyfikowano i dodano nową otwartą kwestię **9.7 — transakcyjność i wycofywalność częściowego przetworzenia**, wynikającą z tego modelu.

### 2026-08-31 (cd.) — Analiza repozytorium `hfb_xmlmap_exporter` (gałąź 19.0)

Ustalono trzy główne elementy procesu głównego jako **stany**: (1) pobranie pliku, (2) przygotowanie do przetwarzania, (3) przetwarzanie.

Na podstawie analizy kodu repozytorium `hfb_xmlmap_exporter` potwierdzono wzorzec architektoniczny XET Base i sposób jego rozszerzania (patrz nowa sekcja 2.1): `communication.log` pozostaje centralną osią przetwarzania; moduł `hfb_apds` rozbuduje go analogicznie do sposobu, w jaki zrobiono to dla obsługi KSeF — przez dodanie własnej, równoległej warstwy pól domenowych (analogicznie do `ksef_operation` / `ksef_status`), a nie przez modyfikację bazowego `state`. Trzy stany procesu APDS mają być realizowane w tym właśnie wzorcu.

### 2026-08-31 (cd. 2) — Doprecyzowanie cykli, etapów i obsługi błędów

Ustalono:
- proces APDS obejmuje **dwa główne cykle** — obsługa pliku głównego (pełnego) i cykliczna obsługa pliku różnicowego, strukturalnie identyczne (te same etapy), różniące się harmonogramem; otwarte pozostaje, czy klient dostarczy je jako pliki odrębne, czy jako jeden plik,
- na rekordzie konfiguracji providera rezerwujemy pole **rodzaj aktualizacji** (pełna / różnicowa),
- pierwotne trzy stany doprecyzowano do **czterech etapów** procesu głównego (sekcja 6.1): (1) pobranie pliku — wymaga danych dostępowych do usługi zdalnej i ścieżki docelowej na serwerze, kończy się bez rozpakowania; (2) rozpakowanie pliku (jeśli paczka) + wstrzyknięcie do tabeli tymczasowej + usuwanie plików fizycznych; (3) przetwarzanie tabel; (4) opracowanie podsumowania i zamknięcie `communication.log`,
- rozpakowanie 7zip przypisane jednoznacznie do etapu 2 (nie etapu 1),
- obsługa błędów infrastrukturalnych (komunikacja, plik, dysk) rozłożona na etapy — patrz tabela w 6.2a,
- wznowienie po błędzie: **kontynuacja od etapu zatrzymania**, nie restart całego cyklu (analogicznie do wzorca `ksef_next_operation`),
- mechanizm retry: na razie **na poziomie providera**, reużycie istniejących pól `communication.provider` (`auto_retry`, `retry_attempts`, `retry_delay`, `notification_users`), bez osobnego licznika prób per etap,
- rozróżnienie błędów przejściowych i trwałych nie jest osobną kategoryzacją — to efekt działania mechanizmu retry (w limicie prób = przejściowy/auto-retry, wyczerpany limit = trwały/wymaga ręcznej interwencji).

Zaktualizowano sekcje 2.1, 6 (przebudowana), 9.7, UC-03, UC-04, UC-10.

### 2026-08-31 (cd. 3) — Konsolidacja do trzech stanów

Ustalono: **etap 4 (podsumowanie i zamknięcie logu) nie jest osobnym stanem** — jest czynnością wykonywaną w ramach stanu 3 (przetwarzanie). Proces główny wraca do **trzech stanów**:

1. Pobranie pliku,
2. Przygotowanie do przetwarzania,
3. Przetwarzanie — obejmujące też ocenę wyniku zbiorczego, podsumowanie i zamknięcie `communication.log`.

Zakończenie stanu 3 prowadzi do jednego z dwóch stanów końcowych: **Zaakceptowane — nowy stan referencyjny** albo **Wymaga interwencji ręcznej** (przy błędzie krytycznym lub negatywnym wyniku kontroli).

Zaktualizowano sekcje 2.1, 6.0, 6.1, 6.1a, 6.2a, UC-08, UC-09, UC-10 — usunięto odniesienia do czterech etapów.

### 2026-08-31 (cd. 4) — Nowa otwarta kwestia: nakładające się przebiegi

Przy projektowaniu mechanizmu `find-or-create` (dokument `APDS_wczytanie_JSON_i_workery.md`) zidentyfikowano lukę: `product.product.default_code` nie ma w rdzeniu Odoo wymuszonej unikalności (zweryfikowane w źródłach 19.0) — przy nakładających się w czasie przebiegach (pełny + różnicowy jednocześnie) możliwy jest wyścig prowadzący do zduplikowanych produktów. Dodano **9.8** jako nową kwestię wymagającą ustalenia z klientem.

### 2026-08-31 (cd. 5) — Odpowiedź klienta: jeden plik, zmienna zawartość

Klient potwierdził: **jeden plik** (ta sama nazwa/lokalizacja), zawartość różnicowa codziennie, zawartość pełna raz w tygodniu (weekend). To zastępuje wcześniejsze założenie o dwóch odrębnych plikach/cyklach.

Konsekwencje:
- zamyka **9.8** — nakładanie się przebiegów strukturalnie wykluczone (jeden trigger, jeden proces),
- zamyka pytanie o termin z sekcji 3 (literówka "31.09.26"),
- otwiera nową kwestię **9.9** — mechanizm rozpoznawania rodzaju zawartości pliku (pełna/różnicowa) na podstawie samej zawartości, nie nazwy pliku,
- unieważnia wcześniejsze założenie, że "rodzaj aktualizacji" to stałe pole konfiguracyjne providera — to teraz wynik rozpoznania per przebieg, zapisywany na `communication.log`.

Zaktualizowano sekcje 3, 5.2, 6.0, 9.8, dodano 9.9.

---

## 12. Status dokumentu

**Status:** projekt koncepcyjny / roboczy

Dokument jest nadrzędnym materiałem projektowym APDS i powinien być aktualizowany wraz z kolejnymi decyzjami.

Kolejne zmiany powinny:
- doprecyzowywać istniejące założenia,
- zamykać otwarte decyzje,
- dodawać nowe przypadki użycia,
- opisywać uzgodnione reguły procesu,
- zachowywać historię istotnych decyzji.

Nie należy usuwać wcześniejszych ustaleń bez odnotowania zmiany decyzji.

# -*- coding: utf-8 -*-
"""
Statyczne mapowanie pól rekordu źródłowego ALIAS -> apds.staging.line.

STATUS: WERSJA TYMCZASOWA (statyczna).

Docelowo ta funkcja ma zostać zastąpiona wywołaniem silnika mapowania
XET, sterowanego definicją szablonu (nie zaszytą w kodzie listą pól
jak tutaj). Sygnatura (dict -> MappingResult) jest zaprojektowana
świadomie tak, żeby ta podmiana nie wymagała zmiany kodu wywołującego
w communication_provider_apds_stage_prepare.py - zmieni się tylko
implementacja tej jednej funkcji.

Zakres pól: wszystkie pola podstawowe rekordu źródłowego, które mają
odpowiadającą kolumnę w apds.staging.line (stan na 2026-09-01, 35 pól
modelu). Mapowanie jest surowym, 1:1 odbiciem danych źródłowych do
stagingu - nie wykonuje żadnej interpretacji ani logiki Odoo (brak
taxes_id, categ_id, uom_id itp.) - to należy do Etapu 3.

Poza zakresem (brak kolumny w stagingu / wymaga dalszego rozpoznania -
patrz ALIAS_Struktura_danych_zrodlowych.md sekcja 14):
images[], compatibility[], conn.repl[]/conn.link[], attributes[],
info, price.srp, price.profit.*, stock.foreign.

Reguła pomijania rekordów (ALIAS_Struktura_danych_zrodlowych.md,
sekcja 7): stock.total == 0 -> rekord pomijany. Zastosowana tutaj,
na poziomie mapowania. Może ulec zmianie po decyzji klienta - patrz
komentarz przy SKIP_ON_ZERO_STOCK poniżej.

Wynik zwracany jako MappingResult (status/data/reason), nie przez
wyjątek - przy skali ~2,12 mln rekordów pomijanie na podstawie
stock.total == 0 jest rutynowym, oczekiwanym wynikiem klasyfikacji
rekordu, nie sytuacją wyjątkową.

Reguła odrzucania rekordów bez wymaganych pól (2026-09-03, po
potwierdzeniu na źródle: 1741 rekordów w pliku ma stock.total > 0
i jednocześnie puste 'name'): brak 'name' -> status="error", rekord
NIGDY nie trafia do product.template jako poprawny (APDS_projekt.md,
sekcja 4.3 - błędne dane nie mogą zastąpić poprawnego stanu).
"""
from typing import NamedTuple, Optional

# Reguła może zostać zmieniona/uchylona po decyzji klienta -
# stąd jako nazwana stała, nie zaszyta bezpośrednio w logice.
SKIP_ON_ZERO_STOCK = True


class MappingResult(NamedTuple):
	status: str			 # "ok" | "skipped" | "error"
	data: Optional[dict]	# wypełnione tylko gdy status == "ok"
	reason: Optional[str]   # wypełnione gdy status == "skipped" albo "error"


def map_alias_record_to_staging(record: dict) -> MappingResult:
	"""Mapuje JEDEN rekord źródłowy ALIAS (dict z JSON) na wynik
	gotowy do zapisu w apds.staging.line albo na decyzję o pominięciu
	lub odrzuceniu jako błędnego.

	:param record: pojedynczy element tablicy JSON (jeden produkt)
	:return: MappingResult ze statusem "ok" (wraz z data), "skipped"
		(wraz z reason) albo "error" (wraz z reason)
	"""
	stock = record.get("stock") or {}
	stock_total = stock.get("total") or 0

	if SKIP_ON_ZERO_STOCK and stock_total == 0:
		return MappingResult(status="skipped", data=None, reason="stock.total == 0")

	prefix = record.get("prefix") or ""
	index = record.get("index") or ""
	default_code = f"{prefix} {index}".strip()

	name = record.get("name") or ""
	if not name.strip():
		return MappingResult(
			status="error",
			data=None,
			reason=f"Brak wymaganej wartości 'name' (default_code={default_code!r})",
		)

	price = record.get("price") or {}
	price_buy = price.get("buy") or {}
	price_sell = price.get("sell") or {}

	category = record.get("category") or {}
	flags = record.get("flags") or {}

	data = {
		# identyfikacja
		"prefix": prefix,
		"index_code": index,
		"default_code": default_code,

		# pola podstawowe
		"name": name,
		"description": record.get("description") or "",
		"producer": record.get("producer") or "",
		"ean": record.get("ean") or "",
		"uom": record.get("uom") or "",
		"pkwiu": record.get("pkwiu") or "",
		"cn": record.get("cn") or "",
		"gtu": record.get("gtu"),
		"tax": record.get("tax") or "",
		"oe": record.get("oe") or "",
		"supplier": record.get("supplier") or "",
		"supplier_id": record.get("supplier_id") or "",
		"discount_group": record.get("discount_group") or "",

		# ceny
		"price_buy_netto": price_buy.get("netto") or 0.0,
		"price_buy_brutto": price_buy.get("brutto") or 0.0,
		"price_sell_netto": price_sell.get("netto") or 0.0,
		"price_sell_brutto": price_sell.get("brutto") or 0.0,

		# stany
		#"stock_local": stock.get("local") or 0,
		#"stock_supplier": stock.get("supplier") or 0,
		#"stock_total": stock_total,

		# kategoria
		"category_id_src": category.get("id"),
		"category_name": category.get("name") or "",

		# flagi (na razie tylko "open" - reszta poza zakresem, patrz
		# nagłówek pliku i APDS_projekt.md sekcja 9.6)
		"flags_open": bool(flags.get("open")),
	}
	return MappingResult(status="ok", data=data, reason=None)

import os

def resolve_source_filepath(staging_dir: str) -> str:
	"""Zwraca ścieżkę do jedynego pliku w katalogu roboczym APDS.
	Katalog jest kontraktem: Etap 1 zostawia tam dokładnie jeden
	plik, niezależnie od jego nazwy - nazwa NIE jest zaszywana
	w kodzie (patrz ustalenie z 2026-09-02)."""
	entries = [
		f for f in os.listdir(staging_dir)
		if os.path.isfile(os.path.join(staging_dir, f))
	]
	if len(entries) != 1:
		raise ValueError(
			f"Oczekiwano dokładnie jednego pliku w {staging_dir}, "
			f"znaleziono {len(entries)}: {entries}"
		)
	return os.path.join(staging_dir, entries[0])

#EoF

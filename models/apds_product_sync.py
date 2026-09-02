# -*- coding: utf-8 -*-
"""
Mapowanie apds.staging.line -> wartości product.template (Etap 3).

STATUS: WERSJA TYMCZASOWA - Blok A (2026-09-02).

Zakres ograniczony do pól niewymagających dodatkowej logiki Odoo:
default_code, name, list_price (z price_sell_netto), standard_price
(z price_buy_netto). Pozostałe pola (taxes_id, categ_id, uom_id,
tagi z flags_open, dostawca) celowo poza zakresem tej iteracji -
wątpliwości co do ich docelowej obsługi patrz APDS_do_wyjasnienia.md.
"""


def staging_line_to_product_vals(line):
	"""Buduje słownik wartości do create()/write() na product.template
	na podstawie jednego rekordu apds.staging.line.

	:param line: rekord apds.staging.line (state == 'draft')
	:return: dict gotowy do product.template.create()/write()
	"""
	return {
		"default_code": line.default_code,
		"name": line.name,
		"list_price": line.price_sell_netto,
		"standard_price": line.price_buy_netto,
	}

#EoF

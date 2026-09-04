# -*- coding: utf-8 -*-
# vim: tabstop=4 softtabstop=0 shiftwidth=4 smarttab expandtab fileformat=unix
#################################################################################
#
# Odoo, Open ERP Source Management Solution
# Copyright (C) 2017-2026 Hadron for Business sp. z o.o. (http://hadronforbusiness.com)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#################################################################################
# UWAGA / NOTICE:
# "XET" oraz nazwa "Hadron for Business" są zastrzeżonymi znakami towarowymi
# "XET" and "Hadron for Business" are trademarks of Hadron for Business sp. z o.o.
#
# Sam kod jest objęty licencją AGPLv3, ale koncepcje, pomysły i rozwiązania
# biznesowe w nim zawarte nie są objęte tą licencją i pozostają własnością
# autora.
# The code is licensed under AGPLv3, but the concepts, ideas and business
# solutions contained herein are not covered by this license and remain the
# property of the author.
#################################################################################
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

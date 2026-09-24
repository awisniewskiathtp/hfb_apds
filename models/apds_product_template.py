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
Rozszerzenie product.template o pole EAN specyficzne dla APDS.

DECYZJA (2026-09-24): APDS NIE zapisuje pola `ean` ze źródła ALIAS
do standardowego `barcode` Odoo. Powód: potwierdzone w danych
źródłowych (kwerenda na apds.staging.line, log_id=19) systemowe
duplikaty EAN w pliku klienta - ten sam fizyczny produkt bywa
wymieniony dwukrotnie, raz pod natywnym prefiksem producenta
(np. "KYB", "HP"), raz pod prefiksem ALIAS "A-P" z kodem producenta
zaszytym w indeksie (np. "A-P KYB KYB553379", "A-P HAN HP114 285).
Skala: ~49 781 par duplikatów EAN na ~2,16 mln rekordów (~4,6%
pliku) - zbyt duża, by traktować jako pomijalny szum.

`barcode` w Odoo ma globalny unique constraint (product.product) -
zapis drugiego z pary powodowałby stały, systemowy konflikt
(ValidationError "Kody kreskowe zostały już przypisane"), niezależnie
od kolejności przetwarzania. `apds_ean` jest zwykłym, NIE-unikalnym
polem przechowującym wartość `ean` ze źródła 1:1 dla KAŻDEGO produktu
APDS, bez walidacji kolizji - decyzja o docelowym mapowaniu EAN na
`barcode` (np. po deduplikacji po stronie klienta, lub z regułą
pierwszeństwa między prefiksami) pozostaje otwarta, patrz
APDS_projekt.md sekcja 9.5.
"""
from odoo import fields, models


class ProductTemplateApds(models.Model):
	_inherit = "product.template"

	apds_ean = fields.Char(
		string="EAN (ALIAS)",
		index=True,
		help="Kod EAN ze źródła ALIAS, zapisywany 1:1 bez walidacji "
			 "unikalności (w odróżnieniu od `barcode`). Wprowadzone, "
			 "bo plik źródłowy zawiera systemowe duplikaty EAN dla "
			 "tego samego produktu pod różnymi prefiksami - patrz "
			 "docstring modułu.",
	)

#EoF

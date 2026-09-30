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
#################################################################################
"""
DECYZJA (2026-09-2x): wyłączenie walidacji unikalności barcode
produkt-produkt (product.product._check_duplicated_product_barcodes,
addons/product/models/product_product.py, Odoo 19 core).

Powód: potwierdzony w danych systemowy duplikat EAN w pliku źródłowym
ALIAS - ten sam fizyczny produkt bywa zgłoszony dwukrotnie pod różnymi
prefiksami (np. natywny "KYB" vs "A-P KYB..."), z tym samym EAN.
Skala: ~49 781 par duplikatów na ~2,16 mln rekordów źródłowych.

Test skanera magazynowego (2026-09-2x) potwierdził, że operator
magazynu poprawnie wybiera właściwy produkt z listy przy zeskanowaniu
zduplikowanego kodu kreskowego - duplikat nie jest więc przeszkodą
operacyjną dla magazynu.

Zakres wyłączenia: TYLKO kolizja produkt-produkt. Walidacja kolizji
barcode z opakowaniami (_check_duplicated_packaging_barcodes,
model product.uom) POZOSTAJE aktywna - to inny, nadal zasadny
przypadek, niezwiązany z tą decyzją.

Wcześniej: pole `ean` ze źródła ALIAS mapowane było na własne pole
`apds_ean` (patrz apds_product_template.py) właśnie po to, żeby
ominąć ten problem bez zmiany zachowania Odoo. Ta decyzja zastępuje
tamto obejście - `apds_ean` zostaje jako techniczne pole robocze
używane przez APDS, a `barcode` będzie zasilany bezpośrednio (patrz
apds_product_sync.py), teraz że walidacja nie blokuje duplikatów.
"""
from odoo import models


class ProductProductApdsBarcode(models.Model):
	_inherit = "product.product"

	def _check_duplicated_product_barcodes(self, barcodes_within_company, company_id):
		# Celowo wyłączone - patrz docstring modułu.
		return

#EoF

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
Rozszerzenie communication.provider (XET Base) o typ 'apds'.

ZWERYFIKOWANA NIESPÓJNOŚĆ W KODZIE BAZOWYM
-------------------------------------------
W `hfb_xmlmap_exporter/models/communication_provider.py`:

- `_provider_model_map()` jest udokumentowana jako punkt rozszerzenia
  ("Pluginy mogą tę metodę rozszerzać przez _inherit"),
- ale `_compute_provider_model` (metoda @api.depends('provider_type'),
  faktycznie ustawiająca przechowywane pole `provider_model`) ma WŁASNY,
  osobny, zahardkodowany słownik `type_to_model` wewnątrz metody i NIE
  woła `_provider_model_map()`.

Skutek: samo rozszerzenie `_provider_model_map()` (co robimy niżej, zgodnie
z udokumentowanym zamiarem) NIE WYSTARCZY, żeby pole `provider_model`
poprawnie się obliczyło dla typu 'apds' - `_compute_provider_model` nadal
zwróci `None`, bo 'apds' nie ma wpisu w jej prywatnym słowniku.

Dlatego poniżej w całości nadpisujemy `_compute_provider_model`, tak żeby
korzystała z `self._provider_model_map()` (czyli z mapy uwzględniającej
rozszerzenia wszystkich modułów provider.*, w tym naszego) zamiast z
zahardkodowanego słownika. To NIE zostało zweryfikowane na żywym Odoo -
działa poprawnie wg analizy statycznej kodu bazowego, ale wymaga testu
instalacyjnego (zob. README.md, sekcja "Do zweryfikowania").
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class CommunicationProvider(models.Model):
	_inherit = "communication.provider"

	provider_type = fields.Selection(
		selection_add=[
			("apds", "ALIAS Product Data Synchronization (APDS)"),
		],
		ondelete={"apds": "set default"},
	)

	trigger_type = fields.Selection(
		selection_add=[
			("apds_streaming", "APDS - przetwarzanie strumieniowe"),
		],
		ondelete={"apds_streaming": "set default"},
	)


	@api.model
	def _provider_model_map(self):
		result = super()._provider_model_map()
		result["apds"] = "communication.provider.apds"
		return result

	@api.depends("provider_type")
	def _compute_provider_model(self):
		"""Nadpisuje wersję bazową - korzysta z _provider_model_map()
		(rozszerzalnej) zamiast z zahardkodowanego słownika w bazowej
		implementacji. Zob. docstring modułu."""
		model_map = self._provider_model_map()
		for record in self:
			record.provider_model = model_map.get(record.provider_type)
			if record.provider_type == "apds" and not record.provider_model:
				_logger.warning(
					"[APDS] provider_type='apds' ale provider_model nie "
					"został obliczony - sprawdź _provider_model_map()."
				)


#EoF

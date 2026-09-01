# -*- coding: utf-8 -*-
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

from odoo import api, fields, models

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

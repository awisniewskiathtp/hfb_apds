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
"""@version 19.0.1.0.0
   @owner  Hadron for Business Sp. z o.o.
   @author Andrzej Wiśniewski (warp3r)
   @date   2026-09-01
"""

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

import logging
_logger = logging.getLogger(__name__)

from .apds_product_sync import staging_line_to_product_vals


class CommunicationLogE3(models.Model):
	_inherit = "communication.log"

	def _apds_stage_process(self):
		"""Etap 3 procesu APDS - przetwarzanie przygotowanych danych.

		Pętla pobiera kolejne partie rekordów apds.staging.line
		(state='draft') przez FOR UPDATE SKIP LOCKED - pozwala to na
		bezpieczne równoległe działanie wielu workerów cron (Blok C,
		ustalenie 2026-09-02) nad tym samym communication.log, bez
		wzajemnej kolizji o te same rekordy.

		Wewnątrz partii każdy rekord jest przetwarzany z osobnym
		SAVEPOINT (Blok D) - błąd pojedynczego rekordu nie niszczy
		pozostałych w tej samej partii (UC-07), rekord trafia do
		state='error' z error_message, przetwarzanie kontynuuje się.

		Po wyczerpaniu partii przez WSZYSTKICH workerów, JEDEN z nich
		(zabezpieczone blokadą wiersza communication.log) wykonuje
		finalizację: wiadomość na chatter, sprzątanie stagingu (Blok F),
		ustawienie apds_result="manual" (Blok E - brak jeszcze progu
		z punktu 9.4).
		"""
		provider = self.provider_id
		config = provider._get_plugin_record()

		if not config:
			raise ValueError(
				"Nie znaleziono konfiguracji providera APDS "
				f"dla communication.log id={self.id}."
			)

		batch_size = config.apds_batch_size

		_logger.info(
			"[APDS] Etap 3 (log_id=%s): worker start, batch_size=%s",
			self.id, batch_size,
		)

		while True:
			reserved = self._apds_process_one_batch(batch_size)
			if reserved == 0:
				break

		self._apds_try_finalize_stage3()

	def _apds_process_one_batch(self, batch_size):
		"""Rezerwuje i przetwarza JEDNĄ partię rekordów stagingowych.

		Zwraca liczbę zarezerwowanych rekordów (0 = nic więcej do
		zrobienia - albo staging pusty, albo wszystko zajęte przez
		innych workerów w tym samym momencie).
		"""
		self.env.cr.execute(
			"""
			SELECT id FROM apds_staging_line
			WHERE communication_log_id = %s AND state = 'draft'
			ORDER BY id
			LIMIT %s
			FOR UPDATE SKIP LOCKED
			""",
			(self.id, batch_size),
		)
		ids = [row[0] for row in self.env.cr.fetchall()]
		if not ids:
			return 0

		lines = self.env["apds.staging.line"].browse(ids)
		created = updated = errored = 0

		for line in lines:
			try:
				with self.env.cr.savepoint():
					vals = staging_line_to_product_vals(line)
					product = self.env["product.template"].search(
						[
							("default_code", "=", line.default_code),
							("active", "=", True),
						],
						order="id desc",
						limit=1,
					)
					if product:
						product.write(vals)
						updated += 1
					else:
						self.env["product.template"].create(vals)
						created += 1
					line.write({"state": "processed"})
			except Exception as exc:
				errored += 1
				line.write({
					"state": "error",
					"error_message": str(exc),
				})
				_logger.warning(
					"[APDS] Etap 3 (log_id=%s): staging_line id=%s "
					"błąd: %s",
					self.id, line.id, exc,
				)

		self.write({
			"apds_records_created": self.apds_records_created + created,
			"apds_records_updated": self.apds_records_updated + updated,
			"apds_records_error": self.apds_records_error + errored,
			"apds_records_processed": (
				self.apds_records_processed + created + updated
			),
		})
		self.env.cr.commit()

		return len(ids)

	def _apds_try_finalize_stage3(self):
		"""Domyka Etap 3 - ale tylko RAZ, nawet jeśli kilku workerów
		(Blok C) jednocześnie wyczerpie dostępne partie stagingu.

		Zabezpieczone blokadą wiersza communication.log (SELECT ...
		FOR UPDATE, bez SKIP LOCKED - tu celowo CHCEMY czekać, nie
		pomijać). Tylko jeden worker naraz wykonuje poniższą sekcję;
		pozostali, po zwolnieniu blokady, widzą już
		apds_operation == 'completed' i kończą bez powtórnej
		finalizacji.
		"""
		self.env.cr.execute(
			"SELECT apds_operation FROM communication_log "
			"WHERE id = %s FOR UPDATE",
			(self.id,),
		)
		current_operation = self.env.cr.fetchone()[0]

		if current_operation == "completed":
			self.env.cr.commit()  # zwalnia blokadę
			return

		remaining = self.env["apds.staging.line"].search_count([
			("communication_log_id", "=", self.id),
			("state", "=", "draft"),
		])
		if remaining:
			self.env.cr.commit()  # zwalnia blokadę, inny worker pracuje
			return

		_logger.info(
			"[APDS] Etap 3 (log_id=%s): koniec. utworzono=%s "
			"zaktualizowano=%s błędy=%s",
			self.id,
			self.apds_records_created,
			self.apds_records_updated,
			self.apds_records_error,
		)

		self.message_post(body=(
			f"Etap 3 (przetwarzanie) zakończony: "
			f"utworzono={self.apds_records_created}, "
			f"zaktualizowano={self.apds_records_updated}, "
			f"błędy={self.apds_records_error}."
		))

		# Blok F: sprzątanie stagingu ograniczone do tego przebiegu
		self.env["apds.staging.line"].search([
			("communication_log_id", "=", self.id),
		]).unlink()

		# Blok E: brak jeszcze progu 9.4 - zawsze manualna weryfikacja
		self.write({
			"apds_result": "manual",
			"apds_operation": "completed",
		})
		self.env.cr.commit()

#EoF

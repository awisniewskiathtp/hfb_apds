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
"""@version 19.0.1.0.0
   @owner  Hadron for Business Sp. z o.o.
   @author Andrzej Wiśniewski (warp3r)
   @date   2026-09-02
"""

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

import logging
_logger = logging.getLogger(__name__)

from .apds_product_sync import staging_line_to_product_vals

import time
from psycopg2.errors import SerializationFailure

# ------------------------------------------------------------------
# DIAGNOSTYKA WYDAJNOŚCI - TYMCZASOWE (faza prototypu, 2026-09-02)
# Włącza logowanie postępu workera co N batchy. Do usunięcia lub
# ustawienia na None, gdy koncepcja zostanie zweryfikowana i wybrany
# zostanie docelowy mechanizm przetwarzania - logowanie kosztuje
# dodatkowe zapytanie (search_count) i nie powinno trafić na
# produkcję w tej formie.
# ------------------------------------------------------------------
LOG_PROGRESS_EVERY_N_BATCHES = 10  # None = wyłączone całkowicie


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

		batch_count = 0
		total_reserved = 0

		while True:
			reserved = self._apds_process_one_batch(batch_size)
			if reserved == 0:
				break

			batch_count += 1
			total_reserved += reserved

			if (
				LOG_PROGRESS_EVERY_N_BATCHES
				and batch_count % LOG_PROGRESS_EVERY_N_BATCHES == 0
			):
				remaining = self.env["apds.staging.line"].search_count([
					("communication_log_id", "=", self.id),
					("state", "=", "draft"),
				])
				_logger.info(
					"[APDS] Etap 3 (log_id=%s): worker postęp - "
					"przetworzono %s rekordów w tym wywołaniu "
					"(%s batchy), pozostało draft=%s",
					self.id, total_reserved, batch_count, remaining,
				)

		self._apds_try_finalize_stage3()

	def _apds_reserve_batch_with_retry(self, batch_size, max_attempts=5):
		"""Rezerwuje partię rekordów stagingowych przez
		SELECT ... FOR UPDATE SKIP LOCKED, z retry na SerializationFailure
		(REPEATABLE READ - patrz docstring _apds_process_one_batch).

		Po nieudanej próbie wymagany jest rollback przed ponowieniem -
		transakcja jest przerwana po SerializationFailure i nie można
		w niej wykonać kolejnego zapytania bez rollbacku.
		"""
		for attempt in range(1, max_attempts + 1):
			try:
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
				return [row[0] for row in self.env.cr.fetchall()]
			except SerializationFailure:
				self.env.cr.rollback()
				_logger.warning(
					"[APDS] Etap 3 (log_id=%s): SerializationFailure przy "
					"rezerwacji partii, próba %s/%s - ponawiam",
					self.id, attempt, max_attempts,
				)
				time.sleep(0.1 * attempt)  # krótki, rosnący odstęp

		raise RuntimeError(
			f"[APDS] Etap 3 (log_id={self.id}): nie udało się "
			f"zarezerwować partii po {max_attempts} próbach "
			f"(SerializationFailure)."
		)

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
			"[APDS] Etap 3 (log_id=%s): koniec przetwarzania.",
			self.id,
		)

		self.message_post(body="Etap 3 (przetwarzanie) zakończony.")


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

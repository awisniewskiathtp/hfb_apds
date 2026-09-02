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

	def _apds_process_one_batch(self, batch_size):
		"""Rezerwuje i przetwarza JEDNĄ partię rekordów stagingowych.

		Zwraca liczbę zarezerwowanych rekordów (0 = nic więcej do
		zrobienia - albo staging pusty, albo wszystko zajęte przez
		innych workerów w tym samym momencie).

		Optymalizacje (2026-09-02, po pierwszym teście wydajności):
		- Product z kontekstem wyłączającym maszynerię mail.thread
		  (chatter/tracking/followers) - obserwowany główny koszt
		  pojedynczego create()/write() w pierwszym teście.
		- JEDNO zapytanie search() na cały batch zamiast N zapytań
		  w pętli (poprzednia wersja robiła search() per rekord).
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

		Product = self.env["product.template"].with_context(
			tracking_disable=True,			# wyłącza pełne field tracking
			mail_create_nolog=True,			# nie loguje "utworzono" na chatterze
			mail_create_nosubscribe=True,	# nie subskrybuje followerów
			mail_notrack=True,			 	# dodatkowe wyłączenie trackingu
		)

		codes = lines.mapped("default_code")
		existing_products = Product.search([
			("default_code", "in", codes),
			("active", "=", True),
		], order="id desc")

		# Blok B: przy duplikacie default_code bierzemy najnowszy (id desc) -
		# setdefault zachowuje PIERWSZE napotkane wystąpienie klucza; przy
		# wynikach posortowanych malejąco po id pierwszy trafiony jest
		# właśnie najnowszy.
		existing_by_code = {}
		for product in existing_products:
			existing_by_code.setdefault(product.default_code, product)

		for line in lines:
			try:
				with self.env.cr.savepoint():
					vals = staging_line_to_product_vals(line)
					product = existing_by_code.get(line.default_code)
					if product:
						product.write(vals)
						updated += 1
					else:
						new_product = Product.create(vals)
						existing_by_code[line.default_code] = new_product
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

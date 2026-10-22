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
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

import logging
_logger = logging.getLogger(__name__)

import os
import csv
import io
import ijson
import json

from .apds_field_mapping import (
	map_alias_record_to_staging,
	map_alias_supplier_record_to_staging,
	resolve_source_filepath,
)

STAGING_COLUMNS = [
	"communication_log_id", "state",
	"prefix", "index_code", "default_code",
	"name", "description", "producer", "ean", "uom", "pkwiu",
	"cn", "gtu", "tax", "oe", "supplier", "supplier_id",
	"discount_group",
	"price_buy_netto", "price_buy_brutto",
	"price_sell_netto", "price_sell_brutto",
	"stock_local", "stock_supplier", "stock_total",
	"category_id_src", "category_name",
	"flags",
	"supplier_ids",
	"error_message",
	"create_uid", "write_uid", "create_date", "write_date",
]


def _row_from_mapping_result(log_id, uid, now, result):
	"""Buduje jeden wiersz (dict kolumna->wartość) dla COPY,
	niezależnie od statusu wyniku mapowania (ok/skipped/error)."""
	row = {col: None for col in STAGING_COLUMNS}
	row.update({
		"communication_log_id": log_id,
		"create_uid": uid,
		"write_uid": uid,
		"create_date": now,
		"write_date": now,
	})

	if result.status == "ok":
		row["state"] = "draft"
		row.update(result.data)
	elif result.status == "skipped":
		row["state"] = "skipped"
		row["error_message"] = result.reason
	elif result.status == "error":
		row["state"] = "error"
		row["error_message"] = result.reason
		if result.data:
			row.update(result.data)

	return row


def _copy_batch_to_staging(cr, rows):
	"""Zapisuje batch wierszy do apds.staging.line przez COPY."""
	buf = io.StringIO()
	writer = csv.writer(buf)
	for row in rows:
		values = []
		for col in STAGING_COLUMNS:
			value = row[col]
			if col in ("flags", "supplier_ids") and value is not None:
				value = json.dumps(value)
			values.append(value)
		writer.writerow(values)
	buf.seek(0)

	columns_sql = ", ".join(STAGING_COLUMNS)
	cr.copy_expert(
		f"COPY apds_staging_line ({columns_sql}) FROM STDIN WITH CSV",
		buf,
	)

class CommunicationLogE2(models.Model):
	_inherit = "communication.log"

	def _apds_stage_prepare(self):
		"""Etap 2 procesu APDS - przygotowanie danych do przetwarzania.
		...
		Ochrona przed równoległym wykonaniem: pending -> running (start,
		_apds_try_acquire) -> done -> apds_stage="process" -> pending
		(koniec, sukces) albo -> error (koniec, wyjątek). Patrz ustalenie
		2026-09-03 (tabela Etap 1/Etap 2).
		"""
		if not self._apds_try_acquire():
			return

		_logger.info(
			"[APDS] Etap 2 (log_id=%s): wykonuję.",
			self.id,
		)

		try:
			# Blok F: staging musi być pozbawiony rekordów przed startem
			# Etapu 2 - sprzątamy WSZYSTKO dla tego providera, nie tylko
			# tego rekordu, bo poprzedni, niedokończony przebieg (inny
			# communication_log_id) mógł zostawić osierocone dane
			# (ustalenie 2026-09-03, po wykryciu 2 123 576 osieroconych
			# wierszy z log_id=2 podczas przebiegu log_id=3).
			self.env.cr.execute("DELETE FROM apds_staging_line")
			self.env.cr.commit()

			provider = self.provider_id
			config = provider._get_plugin_record()
			if not config:
				raise ValueError(
					"Nie znaleziono konfiguracji providera APDS "
					f"dla communication.log id={self.id}."
				)
			filepath = resolve_source_filepath(config.local_staging_dir)
			batch_size = config.apds_batch_size
			skip_zero_stock = config.apds_skip_zero_stock
			record_limit = config.apds_record_limit
			import_type = config.apds_import_type
			limit_reached = False
			_logger.info(
				"[APDS] Etap 2 (log_id=%s): start, plik=%s, batch_size=%s, "
				"wznowienie_od_offsetu=%s",
				self.id, filepath, batch_size, self.apds_last_offset,
			)

			uid = self.env.uid
			counters = {"ok": 0, "skipped": 0, "error": 0}
			batch = []

			now = fields.Datetime.now()

			with open(filepath, "rb") as f:
				records = ijson.items(f, "item")

				for offset, record in enumerate(records):
					if record_limit and offset >= record_limit:
						limit_reached = True
						break
					if offset < self.apds_last_offset:
						continue

					if import_type == "supplier_ids":
						result = map_alias_supplier_record_to_staging(record)
					else:
						result = map_alias_record_to_staging(
							record, skip_zero_stock=skip_zero_stock,
						)

					counters[result.status] += 1

					row = _row_from_mapping_result(self.id, uid, now, result)
					batch.append((offset, row))

					if len(batch) >= batch_size:
						self._apds_flush_batch(batch)
						batch = []

				if batch:
					self._apds_flush_batch(batch)

			self.write({
				"apds_records_total": sum(counters.values()),
				"apds_records_skipped": counters["skipped"],
				"apds_records_error": counters["error"],
			})
			self.env.cr.commit()

			limit_note = (
				f" Przerwano na limicie {record_limit} rekordów."
				if limit_reached else ""
			)

			_logger.info(
				"[APDS] Etap 2 (log_id=%s): koniec. ok=%s skipped=%s error=%s.%s",
				self.id, counters["ok"], counters["skipped"], counters["error"],
				limit_note,
			)

			self.message_post(body=(
				f"Etap 2 (przygotowanie) zakończony: "
				f"OK={counters['ok']}, pominięte={counters['skipped']}, "
				f"błędy={counters['error']}.{limit_note}"
			))

			if import_type == "supplier_ids":
				self._apds_ensure_supplier_partners()

			if config.apds_cleanup_source_file:
				os.remove(filepath)
				_logger.info(
					"[APDS] Etap 2 (log_id=%s): usunięto plik źródłowy %s",
					self.id, filepath,
				)

			self.write({"apds_result": "done"})
			self.env.cr.commit()

			self.write({
				"apds_stage": "process",
				"apds_operation": "process",
				# Ustawiane RAZ, tutaj - to jedyne miejsce chronione
				# wyłącznością (_apds_try_acquire na wejściu do Etapu 2).
				# Dzięki temu żaden z wielu równoległych workerów Etapu 3
				# nie musi (i nie powinien) ponownie zapisywać tego samego
				# stanu przy każdym batchu - patrz
				# communication_provider_apds_stage_process.py.
				"state": "queued",
			})
			self.env.cr.commit()

			self.write({"apds_result": "pending"})
			self.env.cr.commit()

		except Exception:
			self.write({"apds_result": "error"})
			self.env.cr.commit()
			raise


	def _apds_ensure_supplier_partners(self):
		"""apds_import_type = supplier_ids: dla każdego kodu dostawcy ALIAS
		występującego w stagingu tego przebiegu, który nie ma partnera
		(res.partner.ref), tworzy partnera: name = ref = kod dostawcy.
		Dane partnera uzupełnia klient.

		Wykonywane w Etapie 2 - jedynym miejscu chronionym wyłącznością
		(_apds_try_acquire), dzięki czemu równoległe workery Etapu 3 nie
		tworzą duplikatów partnerów. Kody liczone z całego stagingu
		przebiegu (SQL), więc wynik nie zależy od wznowienia Etapu 2.
		"""
		self.env.cr.execute(
			"""
			SELECT DISTINCT jsonb_object_keys(supplier_ids::jsonb)
			FROM apds_staging_line
			WHERE communication_log_id = %s
			  AND state = 'draft'
			  AND supplier_ids IS NOT NULL
			""",
			(self.id,),
		)
		codes = {row[0] for row in self.env.cr.fetchall() if row[0]}
		if not codes:
			return

		Partner = self.env["res.partner"].with_context(
			tracking_disable=True,
			mail_create_nolog=True,
			mail_create_nosubscribe=True,
		)
		existing = set(Partner.with_context(active_test=False).search(
			[("ref", "in", list(codes))]
		).mapped("ref"))
		missing = sorted(codes - existing)
		if not missing:
			return

		Partner.create([
			{
				"name": code,
				"ref": code,
				"is_company": True,
				"supplier_rank": 1,
			}
			for code in missing
		])
		self.env.cr.commit()

		_logger.info(
			"[APDS] Etap 2 (log_id=%s): utworzono partnerów dla kodów "
			"dostawców - %s: %s",
			self.id, len(missing), ", ".join(missing),
		)
		self.message_post(body=(
			f"Utworzono partnerów dla kodów dostawców ({len(missing)}): "
			f"{', '.join(missing)}. Dane partnerów do uzupełnienia."
		))

	def _apds_flush_batch(self, batch):
		"""Zapisuje jeden batch (lista (offset, row)) do stagingu
		i atomowo aktualizuje apds_last_offset - w JEDNEJ transakcji,
		commit dopiero po obu operacjach (patrz ustalenie 2026-09-02,
		Blok 6)."""
		last_offset = batch[-1][0]
		rows = [row for _offset, row in batch]

		_copy_batch_to_staging(self.env.cr, rows)
		self.write({"apds_last_offset": last_offset + 1})

		self.env.cr.commit()


#EoF

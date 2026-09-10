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
Rozszerzenie communication.log o pola specyficzne dla APDS.

DECYZJA ARCHITEKTONICZNA (ustalona, nie do zmiany bez odnotowania)
--------------------------------------------------------------------
`communication.log` pozostaje centralną osią przebiegu, wspólną dla
wszystkich providerów XET Base. `hfb_apds` NIE wprowadza osobnego modelu
procesu (`apds.process`) obok `communication.log`.

Wzorzec: analogicznie do rozszerzenia w `communication_provider_ksef.py`
(pola `ksef_operation` / `ksef_status` dodane przez `_inherit =
"communication.log"`), `hfb_apds` dodaje własną warstwę pól, nie ruszając
bazowego `state` (draft/generated/validated/queued/sent/received/
superseded/error), który opisuje ogólny cykl transportowy wspólny dla
wszystkich providerów.

TRZY ETAPY APDS (APDS_projekt.md, sekcja 6 + ustalenia projektowe)
--------------------------------------------------------------------
1. pobranie pliku (`download`)
2. przygotowanie do przetwarzania (`prepare`) - rozpakowanie 7zip,
   wstrzyknięcie do tabeli tymczasowej (apds.staging.line), sprzątanie
   plików tymczasowych
3. przetwarzanie (`process`) - obejmuje też ocenę wyniku zbiorczego,
   podsumowanie i zamknięcie communication.log

Zakończenie etapu 3 prowadzi do jednego z dwóch wyników końcowych:
	- `accepted`  - "Zaakceptowane — nowy stan referencyjny"
	- `manual`	- "Wymaga interwencji ręcznej"

STATUS: SZKIELET. Pola i selection są zgodne z ustaleniami projektowymi,
ale logika przejść między etapami (kto, kiedy i na jakiej podstawie zmienia
apds_stage/apds_result) NIE jest tu zaimplementowana - to należy do
metod providera w communication_provider_apds.py, obecnie również
szkieletowych.
"""
from odoo import fields, models
import logging
import os
from markupsafe import Markup, escape
_logger = logging.getLogger(__name__)

class CommunicationLog(models.Model):
	_inherit = "communication.log"

	# ------------------------------------------------------------------
	# Etap APDS (niezależny od bazowego 'state' - patrz docstring modułu)
	# ------------------------------------------------------------------
	apds_resource_waiting = fields.Boolean(
		string="Oczekiwanie na zasoby",
		default=False,
	)

	apds_source_size_bytes = fields.Float(
		string="Rozmiar pliku źródłowego",
		digits=(16, 0),
	)

	apds_source_size_ibytes = fields.Integer(
		string="Rozmiar pliku źródłowego",
		help="Rozmiar pliku źródłowego ustalony przed rozpoczęciem Etapu 1.",
	)

	apds_last_offset = fields.Integer(
		string="Ostatni przetworzony offset (Etap 2)",
		default=0,
		help="Indeks (0-based) ostatniego rekordu źródłowego JSON "
			 "potwierdzonego zapisem do apds.staging.line (commit). "
			 "Używane do wznowienia Etapu 2 (UC-10) bez duplikacji "
			 "i bez pominięć.",
	)

	apds_stage = fields.Selection(
		[
			("download", "Etap 1 - Pobranie pliku"),
			("prepare", "Etap 2 - Przygotowanie do przetwarzania"),
			("process", "Etap 3 - Przetwarzanie"),
		],
		string="Etap APDS",
		tracking=True,
		help="Ustawiane wyłącznie na logach communication.log utworzonych "
			 "przez provider 'apds' (provider_type == 'apds').",
	)

	apds_result = fields.Selection(
		[
			("pending", "Oczekuje"),
			("running", "W trakcie realizacji"),
			("done", "Zakończone"),
			("accepted", "Zaakceptowane - nowy stan referencyjny"),
			("manual", "Wymaga interwencji ręcznej"),
			("error", "Błąd"),
		],
		string="Wynik APDS",
		default="pending",
		tracking=True,
	)

	# ------------------------------------------------------------------
	# APDS — sterowanie bieżącą operacją
	# ------------------------------------------------------------------
	apds_operation = fields.Selection(
		[
			("download", "Pobranie pliku"),
			("prepare", "Przygotowanie danych"),
			("process", "Przetwarzanie danych"),
			("completed", "Zakończono"),
			("failed", "Niepowodzenie"),
		],
		string="Bieżąca operacja APDS",
		tracking=True,
		index=True,
		help="Bieżąca operacja APDS wymagająca wykonania przez mechanizm "
			 "przetwarzający communication.log.",
	)

	# ------------------------------------------------------------------
	# Identyfikacja wersji i rodzaju synchronizacji
	# (APDS_projekt.md, sekcje 3 i 5 - do potwierdzenia dokładny sposób
	# ustalania apds_source_version na podstawie metadanych pliku)
	# ------------------------------------------------------------------
	apds_sync_type = fields.Selection(
		[
			("full", "Pełna"),
			("diff", "Różnicowa"),
		],
		string="Rodzaj synchronizacji APDS",
		tracking=True,
	)

	apds_source_version = fields.Char(
		string="Wersja pliku źródłowego",
		help="Identyfikacja wersji pliku (np. na podstawie daty utworzenia "
			 "z metadanych - APDS_projekt.md, sekcja 3). Sposób ustalania "
			 "nieostatecznie potwierdzony.",
	)

	# ------------------------------------------------------------------
	# Podsumowanie Etapu 3 (liczniki - kształt roboczy, do uzgodnienia
	# razem z definicją audytu, APDS_projekt.md UC-11)
	# ------------------------------------------------------------------
	apds_records_total = fields.Integer(string="Rekordy - łącznie")
	apds_records_processed = fields.Integer(string="Rekordy - przetworzone")
	apds_records_skipped = fields.Integer(string="Rekordy - pominięte")
	apds_records_error = fields.Integer(string="Rekordy - błędne")

	apds_records_created = fields.Integer(
		string="Produkty - utworzone",
	)

	apds_records_updated = fields.Integer(
		string="Produkty - zaktualizowane",
	)


	# pomocnicze
	def _apds_try_acquire(self):
		"""Atomowo sprawdza i zmienia apds_result: pending -> running,
		jednocześnie ustawiając state='queued'. Wspólne dla wszystkich
		trzech etapów APDS (2026-09-03 - podział odpowiedzialności:
		dyspozytor tylko reaguje na state, metody etapów go ustawiają).
		Zwraca True, jeśli TEN proces uzyskał prawo do pracy (dokładnie
		jeden wiersz zmieniony), False jeśli rekord nie był w stanie
		'pending' (już przejęty, zakończony, w błędzie itd.)."""
		self.env.cr.execute(
			"UPDATE communication_log "
			"SET apds_result = 'running', state = 'queued' "
			"WHERE id = %s AND apds_result = 'pending'",
			(self.id,),
		)
		acquired = self.env.cr.rowcount == 1
		self.env.cr.commit()
		return acquired


	# ------------------------------------------------------------------
	# CRON — przetwarzanie przebiegów APDS
	#
	#	communication.log
	#			│
	#			└── apds_stage
	#				  │
	#				  ├── download → _apds_stage_download()
	#				  ├── prepare  → _apds_stage_prepare()
	#				  └── process  → _apds_stage_process()
	#
	# ------------------------------------------------------------------
	def _cron_apds_process(self, log_ids=None):
		"""
		Przetwarza aktywne przebiegi APDS zapisane w communication.log.

		Metoda jest dispatcherem procesu. Na podstawie apds_stage przekazuje
		wykonanie do odpowiedniego modułu technicznego stage_*.

		Jeden rekord communication.log reprezentuje jeden przebieg APDS.
		Kolejne wywołania crona kontynuują przebieg od etapu zapisanego
		w rekordzie.

		Techniczna realizacja poszczególnych etapów znajduje się w:
			- communication_provider_apds_stage_download.py
			- communication_provider_apds_stage_prepare.py
			- communication_provider_apds_stage_process.py
		"""

		domain = [
			("provider_id.provider_type", "=", "apds"),
			("apds_result", "=", "pending"),
			("apds_stage", "in", ["download", "prepare", "process"]),
		]

		if log_ids:
			domain.append(("id", "in", log_ids))

		logs = self.search(domain)

		for log in logs:
			try:
				if log.state != "queued":
					log.write({"state": "queued"})

				if log.apds_stage == "download":
					if not log._apds_check_resources("download"):
						continue
					log._apds_stage_download()

				elif log.apds_stage == "prepare":
					if not log._apds_check_resources("prepare"):
						continue
					log._apds_stage_prepare()

				elif log.apds_stage == "process":
					if not log._apds_check_resources("process"):
						continue
					log._apds_stage_process()

			except Exception:
				_logger.exception(
					"[APDS] Błąd przetwarzania communication.log id=%s",
					log.id,
				)
				raise


	def _apds_log_server_stats(self, label):
		"""Zapisuje na chatter bieżący stan zasobów serwera (RAM, dysk,
		obciążenie CPU) - do wywołania przed i po zasobożernych etapach
		APDS, żeby zdiagnozować, czy awarie (2026-09-03) wynikają
		z nasycenia zasobów infrastruktury, nie z logiki aplikacji."""
		import shutil
		from markupsafe import Markup

		try:
			with open("/proc/meminfo") as f:
				meminfo = {}
				for line in f:
					key, val = line.split(":")
					meminfo[key.strip()] = int(val.strip().split()[0])  # kB
			mem_total_gb = meminfo.get("MemTotal", 0) / 1024 / 1024
			mem_available_gb = meminfo.get("MemAvailable", 0) / 1024 / 1024
			mem_used_gb = mem_total_gb - mem_available_gb
		except Exception as exc:
			mem_total_gb = mem_available_gb = mem_used_gb = None
			_logger.warning("[APDS] Nie udało się odczytać /proc/meminfo: %s", exc)

		try:
			disk = shutil.disk_usage("/")
			disk_total_gb = disk.total / 1024 / 1024 / 1024
			disk_used_gb = disk.used / 1024 / 1024 / 1024
			disk_free_gb = disk.free / 1024 / 1024 / 1024
		except Exception as exc:
			disk_total_gb = disk_used_gb = disk_free_gb = None
			_logger.warning("[APDS] Nie udało się odczytać disk_usage: %s", exc)

		try:
			load1, load5, load15 = os.getloadavg()
		except Exception as exc:
			load1 = load5 = load15 = None
			_logger.warning("[APDS] Nie udało się odczytać getloadavg: %s", exc)

		self.env.cr.execute("""
			SELECT count(*)
			FROM apds_staging_line
			WHERE communication_log_id = %s
			  AND state = 'draft'
		""", (self.id,))
		remaining = self.env.cr.fetchone()[0]

		progress_text = f"pozostało: {remaining:,}".replace(",", " ")

		if mem_total_gb is not None and disk_total_gb is not None and load1 is not None:
			self.message_post(
				body=Markup(
					f"<b>{label}</b> — "
					f"RAM dostępny: {mem_available_gb:.1f} GB, "
					f"dysk wolny: {disk_free_gb:.1f} GB, "
					f"load: {load1:.2f} / {load5:.2f} / {load15:.2f}, "
					f"{progress_text}."
				),
				message_type="notification",
			)
		else:
			self.message_post(
				body=Markup(
					f"<b>{label}</b> — "
					f"{progress_text}, "
					f"błąd odczytu części statystyk — patrz log serwera."
				),
				message_type="notification",
			)

	def _apds_generate_process_report(self):
		"""Generuje końcowy raport przebiegu APDS w chatterze.

		Raport korzysta z danych zapisanych w communication.log oraz
		z pomiarów zasobów zapisanych wcześniej w chatterze.
		"""

		self.ensure_one()

		# --------------------------------------------------------------
		# Dane przebiegu
		# --------------------------------------------------------------
		total = self.apds_records_total or 0
		processed = self.apds_records_processed or 0
		skipped = self.apds_records_skipped or 0
		errors = self.apds_records_error or 0
		created = self.apds_records_created or 0
		updated = self.apds_records_updated or 0

		remaining = 0

		# --------------------------------------------------------------
		# Konfiguracja APDS
		# --------------------------------------------------------------
		config = self.provider_id._get_plugin_record()
		batch_size = config.apds_batch_size if config else None

		# --------------------------------------------------------------
		# Analiza chattera - czasy i pomiary zasobów
		# --------------------------------------------------------------
		messages = self.env["mail.message"].search(
			[
				("model", "=", self._name),
				("res_id", "=", self.id),
			],
			order="date asc, id asc",
		)

		stage3_start = None
		stage3_end = None

		ram_values = []
		disk_values = []
		load1_values = []
		load5_values = []
		load15_values = []

		import re

		for message in messages:
			body = message.body or ""

			# ----------------------------------------------------------
			# Czas Etapu 3
			# ----------------------------------------------------------
			if "Etap 3 - start" in body and stage3_start is None:
				stage3_start = message.date

			if "Etap 3 - koniec (finalizacja)" in body:
				stage3_end = message.date

			# ----------------------------------------------------------
			# Pomiary zasobów
			# ----------------------------------------------------------
			match = re.search(
				r"RAM dostępny:\s*(\d+(?:[.,]\d+)?)\s*GB.*?"
				r"dysk wolny:\s*(\d+(?:[.,]\d+)?)\s*GB.*?"
				r"load:\s*(\d+(?:[.,]\d+)?)\s*/\s*"
				r"(\d+(?:[.,]\d+)?)\s*/\s*"
				r"(\d+(?:[.,]\d+)?)",
				body,
			)

			if match:
				def _float(value):
					return float(value.replace(",", "."))

				ram_values.append(_float(match.group(1)))
				disk_values.append(_float(match.group(2)))
				load1_values.append(_float(match.group(3)))
				load5_values.append(_float(match.group(4)))
				load15_values.append(_float(match.group(5)))

		# --------------------------------------------------------------
		# Czas Etapu 3
		# --------------------------------------------------------------
		duration_text = "brak danych"

		if stage3_start and stage3_end:
			duration = stage3_end - stage3_start
			total_seconds = int(duration.total_seconds())

			hours, remainder = divmod(total_seconds, 3600)
			minutes, seconds = divmod(remainder, 60)

			if hours:
				duration_text = (
					f"{hours} godz. {minutes} min {seconds} s"
				)
			elif minutes:
				duration_text = f"{minutes} min {seconds} s"
			else:
				duration_text = f"{seconds} s"

		# --------------------------------------------------------------
		# Wydajność
		# --------------------------------------------------------------
		throughput_text = "brak danych"

		if stage3_start and stage3_end and processed:
			duration_seconds = (
				stage3_end - stage3_start
			).total_seconds()

			if duration_seconds > 0:
				throughput = processed / duration_seconds
				throughput_text = (
					f"{throughput:,.0f}".replace(",", " ")
					+ " rekordów/s"
				)

		# --------------------------------------------------------------
		# Zasoby
		# --------------------------------------------------------------
		def _range_text(values, unit=" GB"):
			if not values:
				return "brak danych"
			minimum = min(values)
			maximum = max(values)

			if minimum == maximum:
				return f"{minimum:.1f}{unit}".replace(".", ",")

			return (
				f"{maximum:.1f} → {minimum:.1f}{unit}"
			).replace(".", ",")

		ram_text = _range_text(ram_values)
		disk_text = _range_text(disk_values)

		load_text = "brak danych"

		if load1_values:
			load_text = (
				f"{min(load1_values):.2f}–{max(load1_values):.2f} / "
				f"{min(load5_values):.2f}–{max(load5_values):.2f} / "
				f"{min(load15_values):.2f}–{max(load15_values):.2f}"
			).replace(".", ",")

		# --------------------------------------------------------------
		# Raport
		# --------------------------------------------------------------
		body = f"""
		<div>
			<h3>Raport przebiegu APDS</h3>

			<p>
				<b>Przebieg:</b> communication.log #{self.id}<br/>
				<b>Status:</b> zakończony
			</p>

			<h4>Etap 1 — pobranie</h4>
			<p>
				<b>Plik:</b> {escape(self.file_name or "-")}<br/>
				<b>Rozmiar:</b> {self.apds_source_size_bytes:,.0f}
				B
			</p>

			<h4>Etap 2 — przygotowanie</h4>
			<p>
				<b>Rekordów źródłowych:</b> {total:,}<br/>
				<b>Przygotowanych:</b> {total - skipped - errors:,}<br/>
				<b>Pominiętych:</b> {skipped:,}<br/>
				<b>Błędnych:</b> {errors:,}
			</p>

			<h4>Etap 3 — przetwarzanie</h4>
			<p>
				<b>Przetworzono:</b> {processed:,}<br/>
				<b>Utworzono produktów:</b> {created:,}<br/>
				<b>Zaktualizowano produktów:</b> {updated:,}<br/>
				<b>Błędy:</b> {errors:,}<br/>
				<b>Pozostało:</b> {remaining:,}<br/>
				<b>Batch:</b> {batch_size or "-"}<br/>
				<b>Czas:</b> {duration_text}<br/>
				<b>Średnia wydajność:</b> {throughput_text}
			</p>

			<h4>Zasoby podczas Etapu 3</h4>
			<p>
				<b>RAM dostępny:</b> {ram_text}<br/>
				<b>Dysk wolny:</b> {disk_text}<br/>
				<b>Load 1 / 5 / 15 min:</b> {load_text}
			</p>

			<h4>Wynik końcowy</h4>
			<p>
				<b>Synchronizacja zakończona.</b>
			</p>
		</div>
		"""

		self.message_post(
			body=Markup(body),
			message_type="notification",
		)


#EoF

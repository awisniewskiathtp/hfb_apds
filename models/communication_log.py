# -*- coding: utf-8 -*-
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
					log._apds_stage_download()

				elif log.apds_stage == "prepare":
					log._apds_stage_prepare()

				elif log.apds_stage == "process":
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

		if mem_total_gb is not None and disk_total_gb is not None and load1 is not None:
			self.message_post(
				body=Markup(f"""
				<b>Stan serwera ({label})</b><br/>
				RAM: {mem_used_gb:.1f} / {mem_total_gb:.1f} GB użyte (dostępne: {mem_available_gb:.1f} GB)<br/>
				Dysk (/): {disk_used_gb:.1f} / {disk_total_gb:.1f} GB użyte (wolne: {disk_free_gb:.1f} GB)<br/>
				Load average (1/5/15 min): {load1:.2f} / {load5:.2f} / {load15:.2f}
				"""),
				message_type="notification",
			)
		else:
			self.message_post(
				body=Markup(f"<b>Stan serwera ({label})</b><br/>Błąd odczytu części statystyk - patrz log serwera."),
				message_type="notification",
			)

#EoF

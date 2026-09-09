# -*- coding: utf-8 -*-

import logging
import os
import shutil
import urllib.request

from odoo import models, _

_logger = logging.getLogger(__name__)

BYTES_PER_GB = 1024 ** 3


class CommunicationLogAPDSResources(models.Model):
	_inherit = "communication.log"

	def _apds_get_mem_available(self):
		with open("/proc/meminfo", "r", encoding="utf-8") as f:
			for line in f:
				key, value = line.split(":", 1)
				if key.strip() == "MemAvailable":
					return int(value.strip().split()[0]) * 1024
		raise RuntimeError("Nie udało się odczytać MemAvailable.")

	def _apds_get_disk_free(self, path):
		os.makedirs(path, exist_ok=True)
		return shutil.disk_usage(path).free

	def _apds_get_source_size(self, config):
		"""Zwraca rozmiar źródła PRZED rozpoczęciem pobierania."""
		if config.apds_download_protocol == "sftp":
			import io
			import paramiko

			transport = paramiko.Transport(
				(config.sftp_host, config.sftp_port)
			)
			try:
				if config.sftp_private_key:
					key = paramiko.RSAKey.from_private_key(
						io.StringIO(config.sftp_private_key)
					)
					transport.connect(
						username=config.sftp_username,
						pkey=key,
					)
				else:
					transport.connect(
						username=config.sftp_username,
						password=config.sftp_password,
					)

				sftp = paramiko.SFTPClient.from_transport(transport)
				try:
					return sftp.stat(config.sftp_remote_path).st_size
				finally:
					sftp.close()
			finally:
				transport.close()

		if config.apds_download_protocol == "http":
			request = urllib.request.Request(
				config.http_url,
				method="HEAD",
			)

			try:
				with urllib.request.urlopen(request, timeout=60) as response:
					value = response.headers.get("Content-Length")
			except Exception as exc:
				raise RuntimeError(
					"[APDS] Nie można ustalić rozmiaru pliku źródłowego "
					f"przez HTTP: {exc}"
				) from exc

			if not value:
				raise RuntimeError(
					"[APDS] Serwer HTTP nie podał Content-Length. "
					"Nie można bezpiecznie wykonać kontroli miejsca "
					"przed pobraniem."
				)

			return int(value)

		raise RuntimeError(
			f"[APDS] Nieznany protokół pobierania: "
			f"{config.apds_download_protocol!r}"
		)

	def _apds_fail_resource_check(self, stage, message):
		"""Krytyczny brak zasobów — kończy przebieg."""
		self.write({
			"apds_stage": stage,
			"apds_operation": "failed",
			"apds_result": "error",
			"state": "error",
		})
		self.env.cr.commit()

		self.message_post(
			body=_(
				"<b>APDS — proces zatrzymany.</b><br/>"
				f"Etap: {stage}<br/>"
				f"Przyczyna: {message}"
			),
			message_type="notification",
		)

		_logger.error(
			"[APDS] log_id=%s: zatrzymano proces z powodu zasobów: %s",
			self.id,
			message,
		)

	def _apds_check_resources(self, stage):
		"""
		Kontrola zasobów przed wejściem do etapu.

		Zwraca:
			True  - etap może zostać uruchomiony
			False - etap nie może zostać uruchomiony
		"""
		provider = self.provider_id
		config = provider._get_plugin_record()

		if not config:
			self._apds_fail_resource_check(
				stage,
				"Nie znaleziono konfiguracji providera APDS.",
			)
			return False

		# --------------------------------------------------------------
		# ETAP 1 — pobieranie
		# --------------------------------------------------------------
		if stage == "download":
			try:
				source_size = self._apds_get_source_size(config)
				free_space = self._apds_get_disk_free(
					config.local_staging_dir
				)
			except Exception as exc:
				self._apds_fail_resource_check(
					stage,
					str(exc),
				)
				return False

			self.write({
				"apds_source_size_bytes": source_size,
			})
			self.env.cr.commit()

			if free_space < source_size:
				self._apds_fail_resource_check(
					stage,
					_(
						"Brak wystarczającego miejsca na pobranie pliku. "
						f"Wymagane: {source_size / BYTES_PER_GB:.2f} GB, "
						f"dostępne: {free_space / BYTES_PER_GB:.2f} GB."
					),
				)
				return False

			return True

		# --------------------------------------------------------------
		# ETAP 2 — przygotowanie / staging
		# --------------------------------------------------------------
		if stage == "prepare":
			source_size = self.apds_source_size_bytes or 0

			try:
				free_space = self._apds_get_disk_free(
					config.local_staging_dir
				)
			except Exception as exc:
				self._apds_fail_resource_check(
					stage,
					str(exc),
				)
				return False

			# W Etapie 2 plik źródłowy nadal istnieje, a dodatkowo
			# powstają dane robocze. Przyjmujemy więc dwukrotność
			# rozmiaru źródła jako minimalny budżet przestrzeni.
			required_space = source_size * 2

			if free_space < required_space:
				self._apds_fail_resource_check(
					stage,
					_(
						"Brak wystarczającego miejsca dla Etapu 2. "
						f"Wymagane: "
						f"{required_space / BYTES_PER_GB:.2f} GB, "
						f"dostępne: "
						f"{free_space / BYTES_PER_GB:.2f} GB."
					),
				)
				return False

			return True

		# --------------------------------------------------------------
		# ETAP 3 — worker
		# --------------------------------------------------------------
		if stage == "process":
			try:
				mem_available = self._apds_get_mem_available()
			except Exception as exc:
				_logger.warning(
					"[APDS] log_id=%s: nie można sprawdzić RAM: %s",
					self.id,
					exc,
				)
				return False

			config_batch_size = config.apds_batch_size

			# Budżet pamięci workera jest oparty na rozmiarze danych
			# przeznaczonych do pojedynczej porcji.
			#
			# Wartość bazowa musi pochodzić z pomiaru/profilowania batcha.
			required_memory = (
				self._apds_estimate_batch_memory(config_batch_size) * 2
			)

			if mem_available < required_memory:
				_logger.warning(
					"[APDS] log_id=%s: worker nie podejmuje batcha — "
					"RAM dostępny %.2f GB, wymagany %.2f GB.",
					self.id,
					mem_available / BYTES_PER_GB,
					required_memory / BYTES_PER_GB,
				)
				return False

			return True

		raise ValueError(f"Nieznany etap APDS: {stage}")

	def _apds_estimate_batch_memory(self, batch_size):
		"""
			Szacuje pamięć potrzebną do przetworzenia pojedynczego batcha.
		"""
		memory_per_record = 1024	# statycznie

		return (batch_size * memory_per_record)

#EoF

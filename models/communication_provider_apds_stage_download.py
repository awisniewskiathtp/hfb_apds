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
import requests
import io
import os
import urllib
import urllib.request
import urllib.error

import logging
_logger = logging.getLogger(__name__)

class CommunicationLogE1(models.Model):
	_inherit = "communication.log"

	def _apds_download_via_sftp(self, provider):
		"""self = communication.log. provider = communication.provider.apds."""
		dest_path = os.path.join(
			provider.local_staging_dir,
			os.path.basename(provider.sftp_remote_path),
		)

		transport = paramiko.Transport((provider.sftp_host, provider.sftp_port))
		try:
			if provider.sftp_private_key:
				key = paramiko.RSAKey.from_private_key(
					io.StringIO(provider.sftp_private_key)
				)
				transport.connect(username=provider.sftp_username, pkey=key)
			else:
				transport.connect(
					username=provider.sftp_username,
					password=provider.sftp_password,
				)

			sftp = paramiko.SFTPClient.from_transport(transport)
			try:
				remote_size = sftp.stat(provider.sftp_remote_path).st_size
				sftp.get(provider.sftp_remote_path, dest_path)
			finally:
				sftp.close()
		finally:
			transport.close()

		actual_size = os.path.getsize(dest_path)
		if actual_size != remote_size:
			raise ValidationError(
				f"[APDS] Niekompletne pobranie SFTP: zdalny rozmiar "
				f"{remote_size} B, lokalny {actual_size} B (plik: {dest_path})."
			)

		return dest_path

	def _apds_download_via_http(self, provider):
		"""Etap 1 - pobranie pliku źródłowego przez HTTP (wget-equivalent).

		self = rekord communication.log bieżącego przebiegu.
		provider = communication.provider.apds (konfiguracja).
		"""
		url = provider.http_url
		dest_path = os.path.join(
			provider.local_staging_dir,
			os.path.basename(url),
		)

		try:
			with urllib.request.urlopen(url, timeout=300) as resp:
				expected_size = int(resp.headers.get("Content-Length", 0))

				with open(dest_path, "wb") as f:
					while True:
						chunk = resp.read(8 * 1024 * 1024)
						if not chunk:
							break
						f.write(chunk)
		except urllib.error.URLError as exc:
			raise ValidationError(
				f"[APDS] Błąd pobrania pliku przez HTTP z {url}: {exc}"
			) from exc

		actual_size = os.path.getsize(dest_path)
		if expected_size and actual_size != expected_size:
			raise ValidationError(
				f"[APDS] Niekompletne pobranie: oczekiwano {expected_size} B, "
				f"otrzymano {actual_size} B (plik: {dest_path})."
			)

		return dest_path

	def _apds_stage_download(self):
		"""Etap 1 procesu APDS - pobranie pliku źródłowego.

		Ochrona przed równoległym wykonaniem: pending -> running (start,
		_apds_try_acquire) -> done -> apds_stage="prepare" -> pending
		(koniec, sukces) albo -> error (koniec, wyjątek).
		"""
		if not self._apds_try_acquire():
			return

		_logger.info("[APDS] Etap 1 (log_id=%s): wykonuję.", self.id)

		try:
			provider = self.provider_id
			config = provider._get_plugin_record()
			if not config:
				raise ValueError(
					"Nie znaleziono konfiguracji providera APDS "
					f"dla communication.log id={self.id}."
				)

			os.makedirs(config.local_staging_dir, exist_ok=True)

			if config.apds_download_protocol == "sftp":
				dest_path = self._apds_download_via_sftp(config)
			elif config.apds_download_protocol == "http":
				dest_path = self._apds_download_via_http(config)
			else:
				raise ValueError(
					"Nieznany protokół pobierania: "
					f"{config.apds_download_protocol!r}."
				)

			self.write({"file_name": os.path.basename(dest_path)})

			_logger.info(
				"[APDS] Etap 1 (log_id=%s): pobrano plik %s (%s B).",
				self.id, dest_path, os.path.getsize(dest_path),
			)

			self.message_post(body=(
				f"Etap 1 (pobranie) zakończony: plik={os.path.basename(dest_path)}, "
				f"rozmiar={os.path.getsize(dest_path)} B, "
				f"protokół={config.apds_download_protocol}."
			))

			self.write({"apds_result": "done"})
			self.env.cr.commit()

			self.write({
				"apds_stage": "prepare",
				"apds_operation": "prepare",
			})
			self.env.cr.commit()

			self.write({"apds_result": "pending"})
			self.env.cr.commit()

		except Exception:
			self.write({"apds_result": "error"})
			self.env.cr.commit()
			raise

#EoF

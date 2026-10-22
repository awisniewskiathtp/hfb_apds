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

from odoo import fields, models


class ApdsWeekday(models.Model):
	"""Słownik dni tygodnia dla harmonogramu konfiguracji APDS.

	number odpowiada datetime.weekday() w Pythonie (0 = poniedziałek,
	6 = niedziela), dzięki czemu _apds_start_time_reached porównuje dzień
	bez mapy w kodzie. Rekordy: data/apds_weekday_data.xml.
	"""
	_name = "apds.weekday"
	_description = "APDS - dzień tygodnia"
	_order = "sequence, number"

	name = fields.Char(string="Dzień tygodnia", required=True, translate=True)
	number = fields.Integer(
		string="Numer dnia (weekday)",
		required=True,
		help="Zgodny z datetime.weekday(): 0 = poniedziałek ... 6 = niedziela.",
	)
	sequence = fields.Integer(default=10)

	_number_uniq = models.Constraint(
		"UNIQUE(number)",
		"Numer dnia tygodnia musi być unikalny.",
	)
#EoF

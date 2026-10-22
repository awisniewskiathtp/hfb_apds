# -*- coding: utf-8 -*-
"""19.0.0.3.17: istniejące konfiguracje APDS dostają wszystkie dni tygodnia.

Nowe pole Many2many apds_weekday_ids nie dostaje wartości domyślnej dla
rekordów istniejących przed aktualizacją; bez tej migracji konfiguracje
z trigger_type = cron_scheduled przestałyby tworzyć przebiegi.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
	env = api.Environment(cr, SUPERUSER_ID, {})
	days = env["apds.weekday"].search([])
	configs = env["communication.provider.apds"].with_context(
		active_test=False
	).search([("apds_weekday_ids", "=", False)])
	if days and configs:
		configs.write({"apds_weekday_ids": [(6, 0, days.ids)]})

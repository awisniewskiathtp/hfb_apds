# -*- coding: utf-8 -*-
#################################################################################
#
# Odoo, Open ERP Source Management Solution
# Copyright (C) 2026 Hadron for business sp. z o.o.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
#################################################################################
# STATUS: szkielet / prototyp roboczy.
#
# Ten moduł rozszerza `hfb_xmlmap_exporter` (XET Base) o provider ALIAS
# Product Data Synchronization (APDS), na wzór providera KSeF i Local Dir
# już obecnych w module bazowym.
#
# Architektura (zob. models/communication_log.py dla uzasadnienia):
#   `communication.log` pozostaje centralną osią przebiegu. `hfb_apds` NIE
#   wprowadza osobnego, równoległego modelu procesu - rozszerza
#   `communication.log` przez `_inherit`, dodając pola specyficzne dla APDS
#   (`apds_stage`, `apds_result`), analogicznie do `ksef_operation` /
#   `ksef_status` w części KSeF.
#
# Dokumenty źródłowe projektu (poza tym repozytorium):
#   - APDS_projekt.md                      - proces biznesowy, przypadki użycia
#   - ALIAS_Struktura_danych_zrodlowych.md - struktura danych wejściowych JSON
#
# Zakres tego szkieletu: rejestracja providera w XET Base, rozszerzenie
# communication.log o trzy stany APDS, model konfiguracji providera z polami
# SFTP, model linii stagingu (Etap 2). Logika Etapów 1-3 jest SZKIELETEM
# (sygnatury i punkty zaczepienia zgodne z kontraktem `communication.provider`
# zweryfikowanym w kodzie `hfb_xmlmap_exporter`), NIE jest to działająca
# implementacja - zob. README.md.
#################################################################################
{
    "name": "ALIAS Product Data Synchronization",
    "version": "19.0.0.1.0",
    "category": "Inventory/Inventory",
    "summary": "Provider APDS (ALIAS) dla XET Base - SZKIELET / PROTOTYP",
    "description": """
ALIAS Product Data Synchronization (APDS) - szkielet modułu
=============================================================
Status: prototyp / roboczy, punkt startowy integracji z hfb_xmlmap_exporter.
Nie do użytku produkcyjnego. Zob. README.md.
""",
    "author": "Hadron for business sp. z o.o.",
    "website": "https://hadron.eu.com",
    "license": "AGPL-3",
    "depends": [
        "base",
        "product",
        "hfb_xmlmap_exporter",
    ],
    "external_dependencies": {
        "python": ["ijson"],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
		"views/communication_log_views.xml",
		"views/communication_provider_views.xml",
        "views/communication_provider_apds_views.xml",
		"views/communication_provider_apds_menu.xml",	
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

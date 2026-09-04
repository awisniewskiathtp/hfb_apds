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
# STATUS: prototyp działający.
#
# Ten moduł rozszerza `hfb_xmlmap_exporter` (XET Base) o provider ALIAS
# Product Data Synchronization (APDS), na wzór providerów dostępnych
# w module bazowym.
#
# Architektura:
#   `communication.log` pozostaje centralną osią przebiegu. `hfb_apds` NIE
#   wprowadza osobnego, równoległego modelu procesu - rozszerza
#   `communication.log` przez `_inherit`, dodając pola specyficzne dla APDS
#   (`apds_stage`, `apds_result`, `apds_operation` oraz dane przebiegu).
#
# Przebieg APDS:
#   Etap 1 — pobranie pliku
#   Etap 2 — przygotowanie danych i zapis do stagingu
#   Etap 3 — równoległe przetwarzanie stagingu
#   Finalizacja — zakończenie przebiegu i czyszczenie stagingu
#
# Etap 3 wykorzystuje wielu workerów Odoo oraz mechanizm
# SELECT ... FOR UPDATE SKIP LOCKED do równoległej rezerwacji partii.
# Konflikty SerializationFailure przy rezerwacji partii są obsługiwane
# przez rollback i ponowienie z backoffem i jitterem.
#
# Dane źródłowe są przetwarzane strumieniowo. Etap 2 zapisuje przygotowane
# rekordy do technicznej tabeli stagingowej PostgreSQL, a Etap 3 synchronizuje
# dane z `product.template`.
#
# Dokumenty źródłowe projektu (poza tym repozytorium):
#   - APDS_projekt.md
#   - ALIAS_Struktura_danych_zrodlowych.md
#
# Moduł jest prototypem przeznaczonym do testów i walidacji procesu na danych
# klienta. Nie jest jeszcze wersją produkcyjną.
#################################################################################
{
    "name": "ALIAS Product Data Synchronization",
    "version": "19.0.0.2.0",
    "category": "Inventory/Inventory",
    "summary": "ALIAS Product Data Synchronization (APDS) for XET Base",
    "description": """
ALIAS Product Data Synchronization (APDS)
==========================================

Provider APDS dla hfb_xmlmap_exporter (XET Base).

Moduł realizuje pełny prototypowy przebieg synchronizacji danych:

    Etap 1 — pobranie pliku
    Etap 2 — przygotowanie danych
    Etap 3 — równoległe przetwarzanie
    Finalizacja

Etap 2 przetwarza źródłowy JSON strumieniowo i zapisuje przygotowane
rekordy do technicznego stagingu PostgreSQL.

Etap 3 wykorzystuje wielu workerów Odoo i równoległą rezerwację partii
przez SELECT ... FOR UPDATE SKIP LOCKED. Dane stagingowe są następnie
stosowane do product.template.

Moduł jest prototypem przeznaczonym do testów i walidacji procesu.
Nie jest przeznaczony do bezpośredniego użycia produkcyjnego.
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

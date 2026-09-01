# -*- coding: utf-8 -*-
"""
apds.staging.line - tabela tymczasowa Etapu 2 (przygotowanie do
przetwarzania).

Powiązanie: `communication_log_id`, NIE osobny model procesu - zgodnie z
decyzją architektoniczną opisaną w communication_log.py.

Pola produktowe odzwierciedlają strukturę źródłową opisaną w
ALIAS_Struktura_danych_zrodlowych.md (sekcje 4, 6, 7). Nie wszystkie pola
źródłowe są tu jeszcze odwzorowane - część (`compatibility[]`, `conn.*`,
`attributes[]`, `info`, `price.srp`, `price.profit.*`, `stock.foreign`)
wymaga dalszego rozpoznania (ten sam dokument, sekcja 14) i celowo nie
została tu dodana, żeby nie sugerować przedwczesnych decyzji.

STATUS: SZKIELET. Struktura tabeli jest zgodna z obecnym stanem mapowania,
ale samo ładowanie danych (streaming JSON + PostgreSQL COPY, wzorem
APDS_projekt.md/ustaleń o wydajności) nie jest tu zaimplementowane.
"""
from odoo import fields, models


class APDSStagingLine(models.Model):
    _name = "apds.staging.line"
    _description = "APDS Staging Line (Etap 2)"
    _order = "id"

    communication_log_id = fields.Many2one(
        "communication.log", required=True, index=True, ondelete="cascade",
        string="Przebieg (communication.log)",
    )

    state = fields.Selection(
        [
            ("draft", "Wczytana"),
            ("processing", "Przetwarzana"),
            ("processed", "Przetworzona"),
            ("skipped", "Pominięta"),
            ("error", "Błąd"),
        ],
        default="draft",
        required=True,
        index=True,
    )
    error_message = fields.Text()

    # Klucz identyfikacyjny produktu (APDS_projekt.md, UC-06)
    prefix = fields.Char(index=True)
    index_code = fields.Char(string="Index", index=True)
    default_code = fields.Char(
        string="default_code (obliczone)",
        index=True,
        help="prefix + ' ' + index - APDS_projekt.md, UC-06.",
    )

    # Pola podstawowe (ALIAS_Struktura_danych_zrodlowych.md, sekcja 4)
    name = fields.Char()
    description = fields.Text()
    producer = fields.Char()
    ean = fields.Char(index=True)
    uom = fields.Char()
    pkwiu = fields.Char()
    cn = fields.Char()
    gtu = fields.Char()
    tax = fields.Char()
    supplier = fields.Char()
    supplier_id = fields.Char()
    oe = fields.Char()
    discount_group = fields.Char()

    # price.* (sekcja 6)
    price_buy_netto = fields.Float()
    price_buy_brutto = fields.Float()
    price_sell_netto = fields.Float()
    price_sell_brutto = fields.Float()

    # stock.* (sekcja 7) - stock_total == 0 => rekord pomijany (ustalona
    # reguła, sekcja 7)
    stock_local = fields.Float()
    stock_supplier = fields.Float()
    stock_total = fields.Float()

    # category.* (sekcja 8)
    category_id_src = fields.Char(string="category.id (źródło)")
    category_name = fields.Char()

    # flags.* (sekcja 9) - jeden reprezentatywny przykład zmapowany,
    # pozostałe do dodania razem z decyzją o docelowej liście tagów
    flags_open = fields.Boolean()

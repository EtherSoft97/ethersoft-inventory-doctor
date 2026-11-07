# -*- coding: utf-8 -*-

import logging

from odoo import _, models
from odoo.tools import float_compare, float_is_zero

from .inventory_doctor_check_registry import get_check_definition

_logger = logging.getLogger(__name__)


class EstInventoryDoctorChecker(models.AbstractModel):
    _name = "est.inventory.doctor.checker"
    _description = "Inventory Doctor Diagnostic Engine"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_scan(self, scan):
        scan.ensure_one()
        checks = [
            self._check_negative_stock,
            self._check_stock_without_valuation,
            self._check_valuation_discrepancy,
            self._check_abnormal_cost,
            self._check_fifo_inconsistency,
            self._check_missing_journal_entries,
            self._check_orphaned_svl,
            self._check_missing_inventory_accounts,
            self._check_inconsistent_cost_method,
            self._check_duplicate_products,
            self._check_missing_routes,
            self._check_warehouse_misconfig,
            self._check_negative_valuation,
            self._check_suspicious_adjustments,
        ]
        Issue = self.env["est.inventory.doctor.issue"]
        errors = []
        for check in checks:
            try:
                with self.env.cr.savepoint():
                    for vals in check(scan):
                        vals["scan_id"] = scan.id
                        vals = Issue.enrich_from_registry(vals)
                        Issue.create(vals)
            except Exception as exc:
                _logger.exception("Inventory Doctor check failed: %s", check.__name__)
                errors.append("%s: %s" % (check.__name__, exc))
        if errors:
            scan.error_message = "\n".join(errors)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _issue_vals(self, check_type, severity, name, **kwargs):
        meta = get_check_definition(check_type)
        vals = {
            "check_type": check_type,
            "severity": severity,
            "name": name,
        }
        if meta:
            vals.setdefault("check_category", meta.get("category"))
            vals.setdefault("resolution_type", meta.get("resolution_type"))
        vals.update(kwargs)
        return vals

    def _product_effective_valuation(self, product, company):
        product = product.with_company(company)
        return product.valuation or company.inventory_valuation

    def _product_effective_cost_method(self, product, company):
        product = product.with_company(company)
        return product.cost_method or company.cost_method

    # ------------------------------------------------------------------
    # Check: Negative stock
    # ------------------------------------------------------------------

    def _check_negative_stock(self, scan):
        self.env.cr.execute("""
            SELECT sq.id, sq.product_id, sq.location_id, sq.quantity
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN product_product pp ON pp.id = sq.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE sl.usage = 'internal'
              AND sq.quantity < 0
              AND (sq.company_id = %s OR sq.company_id IS NULL)
              AND pt.is_storable = true
            ORDER BY sq.quantity ASC
            LIMIT 500
        """, (scan.company_id.id,))
        for quant_id, product_id, location_id, qty in self.env.cr.fetchall():
            product = self.env["product.product"].browse(product_id)
            location = self.env["stock.location"].browse(location_id)
            yield self._issue_vals(
                "negative_stock",
                "critical",
                _("%(product)s has negative stock at %(location)s") % {
                    "product": product.display_name,
                    "location": location.display_name,
                },
                product_id=product_id,
                quant_id=quant_id,
                location_id=location_id,
                qty_actual=qty,
                qty_affected=qty,
                description=_(
                    "On-hand quantity is %(qty)s at location %(location)s.",
                    qty=qty,
                    location=location.display_name,
                ),
                explanation=_(
                    "<p>Negative stock usually means outgoing moves were processed "
                    "before sufficient incoming quantity was recorded, or inventory "
                    "counts were applied incorrectly.</p>"
                ),
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Stock without value
    # ------------------------------------------------------------------

    def _check_stock_without_valuation(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.is_storable = true
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        for product in products:
            if float_compare(product.qty_available, 0, precision_rounding=product.uom_id.rounding) <= 0:
                continue
            if not float_is_zero(product.total_value, precision_rounding=company.currency_id.rounding):
                continue
            if not float_is_zero(product.standard_price, precision_rounding=0.01) and \
               not float_is_zero(product.avg_cost, precision_rounding=0.01):
                # Has cost but still zero total value — still report
                pass
            yield self._issue_vals(
                "stock_without_valuation",
                "critical",
                _("%s has stock but zero inventory value") % product.display_name,
                product_id=product.id,
                categ_id=product.categ_id.id,
                qty_actual=product.qty_available,
                qty_expected=0.0,
                qty_affected=product.qty_available,
                value_affected=product.total_value,
                description=_(
                    "Physical quantity: %(qty)s. Inventory value: %(val)s. "
                    "Standard price: %(std)s. Average cost: %(avg)s.",
                    qty=product.qty_available,
                    val=product.total_value,
                    std=product.standard_price,
                    avg=product.avg_cost,
                ),
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Valuation discrepancy (FIFO remaining qty vs on-hand)
    # ------------------------------------------------------------------

    def _check_valuation_discrepancy(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.is_storable = true
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        for product in products:
            if self._product_effective_cost_method(product, company) != "fifo":
                continue
            if float_is_zero(product.qty_available, precision_rounding=product.uom_id.rounding):
                continue
            remaining_by_move = product._get_remaining_moves().get(product, {})
            remaining_qty = sum(remaining_by_move.values()) if remaining_by_move else 0.0
            qty_diff = product.qty_available - remaining_qty
            if float_is_zero(qty_diff, precision_rounding=product.uom_id.rounding):
                continue
            if float_compare(abs(qty_diff), 0.0001, precision_rounding=product.uom_id.rounding) <= 0:
                continue
            severity = "critical" if abs(qty_diff) > 1 else "warning"
            avg_cost = product.avg_cost or product.standard_price or 0.0
            value_diff = qty_diff * avg_cost
            yield self._issue_vals(
                "valuation_discrepancy",
                severity,
                _("%(product)s: qty mismatch (on-hand %(stock)s vs FIFO remaining %(rem)s)") % {
                    "product": product.display_name,
                    "stock": product.qty_available,
                    "rem": remaining_qty,
                },
                product_id=product.id,
                categ_id=product.categ_id.id,
                qty_actual=product.qty_available,
                qty_expected=remaining_qty,
                qty_affected=qty_diff,
                value_affected=product.total_value,
                value_difference=value_diff,
                description=_(
                    "On-hand: %(stock)s. FIFO remaining stack: %(rem)s. Difference: %(diff)s.",
                    stock=product.qty_available,
                    rem=remaining_qty,
                    diff=qty_diff,
                ),
                diagnosis_data={
                    "qty_on_hand": product.qty_available,
                    "qty_fifo_remaining": remaining_qty,
                    "difference": qty_diff,
                    "estimated_value_difference": value_diff,
                },
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Abnormal costs
    # ------------------------------------------------------------------

    def _check_abnormal_cost(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.is_storable = true
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        for product in products:
            if float_compare(product.qty_available, 0, precision_rounding=product.uom_id.rounding) <= 0:
                continue

            fix_available = False
            fix_safe = False
            severity = "warning"
            name = ""
            recommendation = ""

            if float_is_zero(product.standard_price, precision_rounding=0.01):
                severity = "critical"
                name = _("%s has zero cost with stock on hand") % product.display_name
                recommendation = _(
                    "Set the product cost from average valuation or last purchase price."
                )
                fix_available = not float_is_zero(product.avg_cost, precision_rounding=0.01)
                fix_safe = fix_available
            elif product.avg_cost and product.standard_price:
                ratio = product.standard_price / product.avg_cost if product.avg_cost else 0
                if ratio > 10 or ratio < 0.1:
                    severity = "warning"
                    name = _("%s has cost far from average valuation") % product.display_name
                    recommendation = _(
                        "Review whether standard price (%(std)s) should align with "
                        "average cost (%(avg)s).",
                        std=product.standard_price,
                        avg=product.avg_cost,
                    )
                else:
                    continue
            else:
                continue

            yield self._issue_vals(
                "abnormal_cost",
                severity,
                name,
                product_id=product.id,
                categ_id=product.categ_id.id,
                qty_actual=product.qty_available,
                value_affected=product.total_value,
                description=_(
                    "Standard price: %(std)s. Average cost: %(avg)s. Inventory value: %(val)s.",
                    std=product.standard_price,
                    avg=product.avg_cost,
                    val=product.total_value,
                ),
                recommendation=recommendation,
                fix_available=fix_available,
                fix_safe=fix_safe,
                fix_message=_("Set standard price from average cost"),
                diagnosis_data={
                    "standard_price": product.standard_price,
                    "average_cost": product.avg_cost,
                    "total_value": product.total_value,
                },
            )

    # ------------------------------------------------------------------
    # Check: FIFO inconsistencies
    # ------------------------------------------------------------------

    def _check_fifo_inconsistency(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.is_storable = true
              AND pt.active = true
            LIMIT 3000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        reported = 0
        for product in products:
            if self._product_effective_cost_method(product, company) != "fifo":
                continue
            if float_compare(product.qty_available, 0, precision_rounding=product.uom_id.rounding) <= 0:
                continue
            remaining_by_move = product._get_remaining_moves().get(product, {})
            if not remaining_by_move:
                yield self._issue_vals(
                    "fifo_inconsistency",
                    "critical",
                    _("%s: on-hand stock but empty FIFO remaining stack") % product.display_name,
                    product_id=product.id,
                    categ_id=product.categ_id.id,
                    qty_actual=product.qty_available,
                    description=_(
                        "On-hand quantity is %(qty)s but no valued incoming moves remain "
                        "in the FIFO stack.",
                        qty=product.qty_available,
                    ),
                    fix_available=False,
                )
                reported += 1
                if reported >= 200:
                    return
                continue
            for move, rem_qty in remaining_by_move.items():
                if float_compare(rem_qty, 0, precision_rounding=product.uom_id.rounding) < 0:
                    yield self._issue_vals(
                        "fifo_inconsistency",
                        "critical",
                        _("%(product)s: FIFO move with negative remaining") % {
                            "product": product.display_name,
                        },
                        product_id=product.id,
                        stock_move_id=move.id,
                        qty_actual=rem_qty,
                        value_affected=move.remaining_value,
                        description=_(
                            "Move %(move)s remaining qty: %(qty)s, remaining value: %(val)s.",
                            move=move.display_name,
                            qty=rem_qty,
                            val=move.remaining_value,
                        ),
                        fix_available=False,
                    )
                    reported += 1
                    if reported >= 200:
                        return

    # ------------------------------------------------------------------
    # Check: Missing journal entries (location-valued real-time moves)
    # ------------------------------------------------------------------

    def _check_missing_journal_entries(self, scan):
        company = scan.company_id
        Move = self.env["stock.move"].with_company(company)
        moves = Move.search([
            ("company_id", "=", company.id),
            ("state", "=", "done"),
            ("account_move_id", "=", False),
            "|", ("is_in", "=", True), ("is_out", "=", True),
            ("product_id.is_storable", "=", True),
        ], order="date desc", limit=500)
        for move in moves:
            if self._product_effective_valuation(move.product_id, company) != "real_time":
                continue
            if not move._should_create_account_move():
                continue
            if float_is_zero(move.value, precision_rounding=company.currency_id.rounding):
                continue
            product = move.product_id
            yield self._issue_vals(
                "missing_journal_entry",
                "warning",
                _("%(product)s: valued move without journal entry") % {
                    "product": product.display_name,
                },
                product_id=product.id,
                stock_move_id=move.id,
                value_affected=move.value,
                recommendation=_(
                    "Use auto-fix to regenerate the missing journal entry for this "
                    "location-valued stock move."
                ),
                fix_available=True,
                fix_safe=True,
                fix_message=_("Create missing journal entry from stock move"),
            )

    # ------------------------------------------------------------------
    # Check: Unvalued stock moves (replaces orphaned SVL in Odoo 19)
    # ------------------------------------------------------------------

    def _check_orphaned_svl(self, scan):
        """Done valued moves with zero value while product has a positive cost."""
        company = scan.company_id
        Move = self.env["stock.move"].with_company(company)
        moves = Move.search([
            ("company_id", "=", company.id),
            ("state", "=", "done"),
            "|", ("is_in", "=", True), ("is_out", "=", True),
            ("product_id.is_storable", "=", True),
        ], order="date desc", limit=400)
        for move in moves:
            if not float_is_zero(move.value, precision_rounding=company.currency_id.rounding):
                continue
            if float_is_zero(move.quantity, precision_rounding=move.product_uom.rounding):
                continue
            product = move.product_id.with_company(company)
            if float_is_zero(product.standard_price, precision_rounding=0.01) and \
               float_is_zero(product.avg_cost, precision_rounding=0.01):
                continue
            yield self._issue_vals(
                "orphaned_svl",
                "warning",
                _("%(product)s: valued move with zero value") % {
                    "product": product.display_name,
                },
                product_id=product.id,
                stock_move_id=move.id,
                qty_affected=move.quantity,
                value_affected=0.0,
                description=_(
                    "Move %(move)s is marked valued but has value 0. "
                    "Product standard price: %(std)s.",
                    move=move.display_name,
                    std=product.standard_price,
                ),
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Missing inventory accounts
    # ------------------------------------------------------------------

    def _check_missing_inventory_accounts(self, scan):
        company = scan.company_id
        categories = self.env["product.category"].with_company(company).search([])
        company_has_valuation = bool(company.account_stock_valuation_id)
        company_has_journal = bool(company.account_stock_journal_id)

        for categ in categories:
            valuation = categ.property_valuation or company.inventory_valuation
            if valuation != "real_time":
                continue
            accounts_missing = []
            if not categ.property_stock_valuation_account_id and not company_has_valuation:
                accounts_missing.append(_("Stock Valuation Account"))
            if not categ.property_stock_journal and not company_has_journal:
                accounts_missing.append(_("Stock Journal"))
            if not accounts_missing:
                continue

            parent = categ.parent_id.with_company(company) if categ.parent_id else False
            can_inherit = bool(parent) and any([
                parent.property_stock_valuation_account_id,
                parent.property_stock_journal,
            ])

            yield self._issue_vals(
                "missing_inventory_account",
                "warning" if can_inherit else "critical",
                _("%(categ)s: missing %(accounts)s") % {
                    "categ": categ.display_name,
                    "accounts": ", ".join(accounts_missing),
                },
                categ_id=categ.id,
                fix_available=can_inherit,
                fix_safe=can_inherit,
                fix_message=_("Copy stock valuation settings from parent category"),
            )

    # ------------------------------------------------------------------
    # Check: Inconsistent costing method
    # ------------------------------------------------------------------

    def _check_inconsistent_cost_method(self, scan):
        company = scan.company_id
        if not company.cost_method:
            yield self._issue_vals(
                "inconsistent_cost_method",
                "critical",
                _("Company %(company)s has no default costing method") % {
                    "company": company.display_name,
                },
                description=_("Set a default costing method on the company."),
                fix_available=False,
            )
        categories = self.env["product.category"].with_company(company).search([])
        for categ in categories:
            if categ.property_cost_method or company.cost_method:
                continue
            product_count = self.env["product.template"].search_count([
                ("categ_id", "child_of", categ.id),
                ("is_storable", "=", True),
            ])
            if product_count:
                yield self._issue_vals(
                    "inconsistent_cost_method",
                    "critical",
                    _("%(categ)s has no costing method configured") % {
                        "categ": categ.display_name,
                    },
                    categ_id=categ.id,
                    description=_("%s storable products affected.", product_count),
                    fix_available=False,
                )

    # ------------------------------------------------------------------
    # Check: Duplicate products
    # ------------------------------------------------------------------

    def _check_duplicate_products(self, scan):
        for field, label in [("default_code", _("Internal Reference")), ("barcode", _("Barcode"))]:
            self.env.cr.execute(f"""
                SELECT {field}, array_agg(pp.id ORDER BY pp.id)
                FROM product_product pp
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE {field} IS NOT NULL AND {field} != ''
                  AND pt.active = true
                GROUP BY {field}
                HAVING COUNT(*) > 1
                LIMIT 50
            """)
            for code, product_ids in self.env.cr.fetchall():
                products = self.env["product.product"].browse(product_ids)
                yield self._issue_vals(
                    "duplicate_product",
                    "warning",
                    _("Duplicate %(label)s: %(code)s (%(count)s products)") % {
                        "label": label,
                        "code": code,
                        "count": len(product_ids),
                    },
                    description=", ".join(products.mapped("display_name")),
                    diagnosis_data={"field": field, "value": code, "product_ids": product_ids},
                    fix_available=False,
                )

    # ------------------------------------------------------------------
    # Check: Missing routes
    # ------------------------------------------------------------------

    def _check_missing_routes(self, scan):
        products = self.env["product.template"].search([
            ("is_storable", "=", True),
            ("route_ids", "=", False),
            ("categ_id.total_route_ids", "=", False),
        ], limit=200)
        for tmpl in products:
            yield self._issue_vals(
                "missing_route",
                "info",
                _("%s has no routes configured") % tmpl.display_name,
                product_tmpl_id=tmpl.id,
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Warehouse misconfiguration
    # ------------------------------------------------------------------

    def _check_warehouse_misconfig(self, scan):
        warehouses = self.env["stock.warehouse"].search([
            ("company_id", "=", scan.company_id.id),
        ])
        for wh in warehouses:
            issues = []
            if not wh.lot_stock_id:
                issues.append(_("missing stock location"))
            if not wh.view_location_id:
                issues.append(_("missing view location"))
            if wh.delivery_steps == "pick_pack_ship" and not wh.wh_pack_stock_loc_id:
                issues.append(_("pack location missing for 3-step delivery"))
            if issues:
                yield self._issue_vals(
                    "warehouse_misconfig",
                    "warning",
                    _("%(wh)s: %(issues)s") % {
                        "wh": wh.display_name,
                        "issues": ", ".join(issues),
                    },
                    warehouse_id=wh.id,
                    fix_available=False,
                )

    # ------------------------------------------------------------------
    # Check: Negative valuation
    # ------------------------------------------------------------------

    def _check_negative_valuation(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.is_storable = true
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        for product in products:
            if product.total_value >= 0:
                continue
            yield self._issue_vals(
                "negative_valuation",
                "critical",
                _("%s has negative inventory valuation") % product.display_name,
                product_id=product.id,
                categ_id=product.categ_id.id,
                value_affected=product.total_value,
                qty_actual=product.qty_available,
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Suspicious adjustments
    # ------------------------------------------------------------------

    def _check_suspicious_adjustments(self, scan):
        self.env.cr.execute("""
            SELECT sm.id, sm.product_id, sm.product_qty, sm.reference
            FROM stock_move sm
            JOIN stock_location sl_src ON sl_src.id = sm.location_id
            JOIN stock_location sl_dest ON sl_dest.id = sm.location_dest_id
            WHERE sm.state = 'done'
              AND sm.company_id = %s
              AND (
                  (sl_src.usage = 'inventory' AND sl_dest.usage = 'internal')
                  OR (sl_src.usage = 'internal' AND sl_dest.usage = 'inventory')
              )
              AND sm.product_qty > 1000
              AND sm.date >= (NOW() - INTERVAL '90 days')
            ORDER BY sm.product_qty DESC
            LIMIT 50
        """, (scan.company_id.id,))
        for move_id, product_id, qty, ref in self.env.cr.fetchall():
            product = self.env["product.product"].browse(product_id)
            yield self._issue_vals(
                "suspicious_adjustment",
                "warning",
                _("%(product)s: large inventory adjustment (%(qty)s)") % {
                    "product": product.display_name,
                    "qty": qty,
                },
                product_id=product_id,
                stock_move_id=move_id,
                qty_affected=qty,
                description=_("Reference: %s", ref or "-"),
                fix_available=False,
            )

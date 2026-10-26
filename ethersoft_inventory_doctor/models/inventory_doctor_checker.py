# -*- coding: utf-8 -*-

import logging

from odoo import _, api, fields, models
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

    def _category_ids_with(self, scan, field_name, value):
        categories = self.env["product.category"].with_company(scan.company_id).search([])
        return categories.filtered(lambda c: c[field_name] == value).ids

    def _resolve_svl_stock_move(self, svl):
        move = svl.stock_move_id
        if not move and svl.stock_valuation_layer_id:
            move = svl.stock_valuation_layer_id.stock_move_id
        return move

    def _infer_valuation_direction(self, move, svl):
        """Infer in/out when Odoo does not classify the move (unpicked lines, inventory adj)."""
        src = move.location_id
        dest = move.location_dest_id
        src_valued = src._should_be_valued() if src else False
        dest_valued = dest._should_be_valued() if dest else False
        if src_valued and not dest_valued:
            return "out"
        if not src_valued and dest_valued:
            return "in"
        if dest and dest.usage in ("inventory", "customer", "production"):
            return "out"
        if src and src.usage in ("inventory", "supplier", "production"):
            return "in"
        if svl.quantity < 0 or svl.value < 0:
            return "out"
        if svl.quantity > 0 or svl.value > 0:
            return "in"
        return False

    def _prepare_forced_account_move_vals(self, move, svl, company):
        """Build a stock valuation journal entry when standard Odoo classification is empty."""
        move = move.with_company(company)
        journal_id, acc_src, acc_dest, acc_valuation = move._get_accounting_data_for_valuation()
        direction = self._infer_valuation_direction(move, svl)
        qty = svl.quantity
        cost = svl.value
        description = svl.description or move.display_name
        force_date = fields.Date.to_date(svl.create_date) if svl.create_date else fields.Date.context_today(self)
        move = move.with_context(force_period_date=force_date)
        if direction == "out":
            return move._prepare_account_move_vals(
                acc_valuation, acc_dest, journal_id, qty, description, svl.id, -cost
            )
        if direction == "in":
            return move._prepare_account_move_vals(
                acc_src, acc_valuation, journal_id, qty, description, svl.id, cost
            )
        return False

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
              AND pt.type = 'product'
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
    # Check: Stock without valuation
    # ------------------------------------------------------------------

    def _check_stock_without_valuation(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.type = 'product'
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        real_time_categ_ids = set(self._category_ids_with(scan, "property_valuation", "real_time"))
        for product in products:
            if product.categ_id.id not in real_time_categ_ids:
                continue
            if float_compare(product.qty_available, 0, precision_rounding=product.uom_id.rounding) <= 0:
                continue
            if not float_is_zero(product.quantity_svl, precision_rounding=product.uom_id.rounding):
                continue
            yield self._issue_vals(
                "stock_without_valuation",
                "critical",
                _("%s has stock but no valuation layers") % product.display_name,
                product_id=product.id,
                categ_id=product.categ_id.id,
                qty_actual=product.qty_available,
                qty_expected=0.0,
                qty_affected=product.qty_available,
                description=_(
                    "Physical quantity: %(qty)s. Valuation quantity: 0.",
                    qty=product.qty_available,
                ),
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Valuation discrepancy
    # ------------------------------------------------------------------

    def _check_valuation_discrepancy(self, scan):
        company = scan.company_id
        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.type = 'product'
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        real_time_categ_ids = set(self._category_ids_with(scan, "property_valuation", "real_time"))
        for product in products:
            if product.categ_id.id not in real_time_categ_ids:
                continue
            qty_diff = product.qty_available - product.quantity_svl
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
                _("%(product)s: qty mismatch (stock %(stock)s vs SVL %(svl)s)") % {
                    "product": product.display_name,
                    "stock": product.qty_available,
                    "svl": product.quantity_svl,
                },
                product_id=product.id,
                categ_id=product.categ_id.id,
                qty_actual=product.qty_available,
                qty_expected=product.quantity_svl,
                qty_affected=qty_diff,
                value_affected=product.value_svl,
                value_difference=value_diff,
                description=_(
                    "On-hand: %(stock)s. Valuation layers total: %(svl)s. Difference: %(diff)s.",
                    stock=product.qty_available,
                    svl=product.quantity_svl,
                    diff=qty_diff,
                ),
                diagnosis_data={
                    "qty_on_hand": product.qty_available,
                    "qty_svl": product.quantity_svl,
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
            WHERE pt.type = 'product'
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
                fix_available = (
                    product.valuation == "real_time"
                    and not float_is_zero(product.avg_cost, precision_rounding=0.01)
                )
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
                value_affected=product.value_svl,
                description=_(
                    "Standard price: %(std)s. Average cost: %(avg)s. Valuation value: %(val)s.",
                    std=product.standard_price,
                    avg=product.avg_cost,
                    val=product.value_svl,
                ),
                recommendation=recommendation,
                fix_available=fix_available,
                fix_safe=fix_safe,
                fix_message=_("Set standard price from average cost"),
                diagnosis_data={
                    "standard_price": product.standard_price,
                    "average_cost": product.avg_cost,
                    "valuation_value": product.value_svl,
                },
            )

    # ------------------------------------------------------------------
    # Check: FIFO inconsistencies
    # ------------------------------------------------------------------

    def _check_fifo_inconsistency(self, scan):
        fifo_categ_ids = self._category_ids_with(scan, "property_cost_method", "fifo")
        if not fifo_categ_ids:
            return

        self.env.cr.execute("""
            SELECT svl.id, svl.product_id, svl.remaining_qty, svl.remaining_value
            FROM stock_valuation_layer svl
            JOIN product_product pp ON pp.id = svl.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE svl.company_id = %s
              AND pt.categ_id = ANY(%s)
              AND (svl.remaining_qty < 0 OR svl.remaining_value < 0)
            ORDER BY svl.remaining_qty ASC
            LIMIT 200
        """, (scan.company_id.id, fifo_categ_ids))
        for svl_id, product_id, rem_qty, rem_val in self.env.cr.fetchall():
            product = self.env["product.product"].browse(product_id)
            yield self._issue_vals(
                "fifo_inconsistency",
                "critical",
                _("%(product)s: FIFO layer with negative remaining") % {
                    "product": product.display_name,
                },
                product_id=product_id,
                svl_id=svl_id,
                qty_actual=rem_qty,
                value_affected=rem_val,
                description=_(
                    "Layer remaining qty: %(qty)s, remaining value: %(val)s.",
                    qty=rem_qty,
                    val=rem_val,
                ),
                fix_available=False,
            )

        self.env.cr.execute("""
            SELECT pp.id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.categ_id = ANY(%s)
              AND pt.type = 'product'
              AND EXISTS (
                  SELECT 1 FROM stock_valuation_layer svl
                  WHERE svl.product_id = pp.id
                    AND svl.company_id = %s
                    AND svl.quantity < 0
                    AND svl.stock_valuation_layer_id IS NULL
              )
            LIMIT 100
        """, (fifo_categ_ids, scan.company_id.id))
        for (product_id,) in self.env.cr.fetchall():
            product = self.env["product.product"].with_company(scan.company_id).browse(product_id)
            if float_is_zero(product.quantity_svl, precision_rounding=product.uom_id.rounding):
                yield self._issue_vals(
                    "fifo_inconsistency",
                    "warning",
                    _("%s: FIFO outgoing layer without linked consumption") % product.display_name,
                    product_id=product_id,
                    fix_available=False,
                )

    # ------------------------------------------------------------------
    # Check: Missing journal entries
    # ------------------------------------------------------------------

    def _check_missing_journal_entries(self, scan):
        real_time_categ_ids = self._category_ids_with(scan, "property_valuation", "real_time")
        if not real_time_categ_ids:
            return

        self.env.cr.execute("""
            SELECT svl.id, svl.product_id, svl.value,
                   COALESCE(svl.stock_move_id, parent.stock_move_id) AS move_id
            FROM stock_valuation_layer svl
            JOIN product_product pp ON pp.id = svl.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN stock_valuation_layer parent
                   ON parent.id = svl.stock_valuation_layer_id
            WHERE svl.company_id = %s
              AND pt.categ_id = ANY(%s)
              AND svl.account_move_id IS NULL
              AND ABS(svl.value) > 0.0001
            ORDER BY svl.create_date DESC
            LIMIT 300
        """, (scan.company_id.id, real_time_categ_ids))
        for svl_id, product_id, value, move_id in self.env.cr.fetchall():
            product = self.env["product.product"].browse(product_id)
            can_autofix = bool(move_id)
            yield self._issue_vals(
                "missing_journal_entry",
                "warning",
                _("%(product)s: valuation layer without journal entry") % {
                    "product": product.display_name,
                },
                product_id=product_id,
                svl_id=svl_id,
                stock_move_id=move_id or False,
                value_affected=value,
                recommendation=_(
                    "Use auto-fix to regenerate missing journal entries when a stock "
                    "move is linked."
                ) if can_autofix else _(
                    "This layer has no linked stock move. Review and post manually."
                ),
                fix_available=can_autofix,
                fix_safe=can_autofix,
                fix_message=_("Create missing journal entry via valuation layer validation")
                if can_autofix else False,
            )

    # ------------------------------------------------------------------
    # Check: Orphaned SVL
    # ------------------------------------------------------------------

    def _check_orphaned_svl(self, scan):
        self.env.cr.execute("""
            SELECT svl.id, svl.product_id, svl.quantity, svl.value
            FROM stock_valuation_layer svl
            WHERE svl.company_id = %s
              AND svl.stock_move_id IS NULL
              AND svl.stock_valuation_layer_id IS NULL
              AND svl.account_move_line_id IS NULL
              AND ABS(svl.quantity) > 0.0001
            ORDER BY svl.create_date DESC
            LIMIT 200
        """, (scan.company_id.id,))
        for svl_id, product_id, qty, value in self.env.cr.fetchall():
            product = self.env["product.product"].browse(product_id)
            yield self._issue_vals(
                "orphaned_svl",
                "warning",
                _("%(product)s: orphaned valuation layer") % {
                    "product": product.display_name,
                },
                product_id=product_id,
                svl_id=svl_id,
                qty_affected=qty,
                value_affected=value,
                fix_available=False,
            )

    # ------------------------------------------------------------------
    # Check: Missing inventory accounts
    # ------------------------------------------------------------------

    def _check_missing_inventory_accounts(self, scan):
        company = scan.company_id
        categories = self.env["product.category"].with_company(company).search([])
        for categ in categories:
            if categ.property_valuation != "real_time":
                continue
            accounts_missing = []
            if not categ.property_stock_valuation_account_id:
                accounts_missing.append(_("Stock Valuation Account"))
            if not categ.property_stock_account_output_categ_id:
                accounts_missing.append(_("Stock Output Account"))
            if not categ.property_stock_account_input_categ_id:
                accounts_missing.append(_("Stock Input Account"))
            if not accounts_missing:
                continue

            parent = categ.parent_id.with_company(company) if categ.parent_id else False
            can_inherit = bool(parent) and any([
                parent.property_stock_valuation_account_id,
                parent.property_stock_account_output_categ_id,
                parent.property_stock_account_input_categ_id,
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
                fix_message=_("Copy stock accounts from parent category"),
            )

    # ------------------------------------------------------------------
    # Check: Inconsistent costing method
    # ------------------------------------------------------------------

    def _check_inconsistent_cost_method(self, scan):
        categories = self.env["product.category"].with_company(scan.company_id).search([])
        for categ in categories:
            if not categ.property_cost_method:
                product_count = self.env["product.template"].search_count([
                    ("categ_id", "child_of", categ.id),
                    ("type", "=", "product"),
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
            ("type", "=", "product"),
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
            WHERE pt.type = 'product'
              AND pt.active = true
            LIMIT 5000
        """)
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products = self.env["product.product"].browse(product_ids).with_company(company)
        real_time_categ_ids = set(self._category_ids_with(scan, "property_valuation", "real_time"))
        for product in products:
            if product.categ_id.id not in real_time_categ_ids:
                continue
            if product.value_svl >= 0:
                continue
            yield self._issue_vals(
                "negative_valuation",
                "critical",
                _("%s has negative inventory valuation") % product.display_name,
                product_id=product.id,
                categ_id=product.categ_id.id,
                value_affected=product.value_svl,
                qty_actual=product.quantity_svl,
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

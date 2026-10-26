# -*- coding: utf-8 -*-

from odoo import _, api, fields, models

from .inventory_doctor_check_registry import get_check_definition


class EstInventoryDoctorIssue(models.Model):
    _name = "est.inventory.doctor.issue"
    _description = "Inventory Doctor Issue"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "severity desc, check_type, id"

    scan_id = fields.Many2one(
        "est.inventory.doctor.scan",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="scan_id.company_id",
        store=True,
        index=True,
    )
    detected_date = fields.Datetime(related="scan_id.scan_date", store=True, index=True)

    check_type = fields.Selection(
        selection="_selection_check_type",
        required=True,
        index=True,
    )
    check_category = fields.Selection([
        ("stock", "Stock"),
        ("valuation", "Valuation"),
        ("accounting", "Accounting"),
        ("configuration", "Configuration"),
        ("master_data", "Master Data"),
    ], index=True)
    severity = fields.Selection(
        [
            ("info", "Info"),
            ("warning", "Warning"),
            ("critical", "Critical"),
        ],
        required=True,
        default="warning",
        index=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("open", "Open"),
            ("in_progress", "In Progress"),
            ("fixed", "Fixed"),
            ("ignored", "Ignored"),
            ("false_positive", "False Positive"),
        ],
        default="open",
        required=True,
        index=True,
        tracking=True,
    )
    resolution_type = fields.Selection([
        ("auto_fix", "Auto Fix"),
        ("manual", "Manual Review"),
        ("guided", "Guided Resolution"),
    ], index=True)

    name = fields.Char(required=True, tracking=True)
    description = fields.Text()
    explanation = fields.Html()
    why_it_matters = fields.Html()
    possible_causes = fields.Text()
    recommendation = fields.Text()
    diagnosis_data = fields.Json()

    product_id = fields.Many2one("product.product", index=True)
    product_tmpl_id = fields.Many2one("product.template", index=True)
    categ_id = fields.Many2one("product.category", index=True)
    stock_move_id = fields.Many2one("stock.move", index=True)
    svl_id = fields.Many2one("stock.valuation.layer", index=True)
    account_move_id = fields.Many2one("account.move", index=True)
    quant_id = fields.Many2one("stock.quant", index=True)
    warehouse_id = fields.Many2one("stock.warehouse", index=True)
    location_id = fields.Many2one("stock.location", index=True)

    internal_reference = fields.Char(related="product_id.default_code", store=True)
    barcode = fields.Char(related="product_id.barcode", store=True)

    qty_expected = fields.Float(digits="Product Unit of Measure")
    qty_actual = fields.Float(digits="Product Unit of Measure")
    qty_affected = fields.Float(digits="Product Unit of Measure", string="Difference")
    value_affected = fields.Monetary(currency_field="currency_id", string="Value Impact")
    value_difference = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
    )

    fix_available = fields.Boolean(default=False)
    fix_safe = fields.Boolean(
        string="Safe Auto-Fix",
        default=False,
    )
    fix_message = fields.Char()
    fix_date = fields.Datetime(readonly=True)
    fix_user_id = fields.Many2one("res.users", readonly=True)
    assigned_user_id = fields.Many2one("res.users", tracking=True)
    internal_note = fields.Text()

    check_type_label = fields.Char(compute="_compute_labels")
    severity_label = fields.Char(compute="_compute_labels")
    diagnosis_summary_html = fields.Html(compute="_compute_diagnosis_summary_html", sanitize=False)

    @staticmethod
    def _selection_check_type():
        return [
            ("negative_stock", "Negative Stock"),
            ("stock_without_valuation", "Stock Without Valuation"),
            ("valuation_discrepancy", "Valuation Discrepancy"),
            ("abnormal_cost", "Abnormal Cost"),
            ("fifo_inconsistency", "FIFO Inconsistency"),
            ("missing_journal_entry", "Missing Journal Entry"),
            ("orphaned_svl", "Orphaned Valuation Layer"),
            ("missing_inventory_account", "Missing Inventory Account"),
            ("inconsistent_cost_method", "Inconsistent Costing Method"),
            ("duplicate_product", "Duplicate Product"),
            ("missing_route", "Missing Route"),
            ("warehouse_misconfig", "Warehouse Misconfiguration"),
            ("negative_valuation", "Negative Inventory Valuation"),
            ("suspicious_adjustment", "Suspicious Stock Adjustment"),
        ]

    @api.depends("check_type", "severity")
    def _compute_labels(self):
        check_labels = dict(self._selection_check_type())
        severity_labels = dict(self._fields["severity"].selection)
        for issue in self:
            issue.check_type_label = check_labels.get(issue.check_type, issue.check_type)
            issue.severity_label = severity_labels.get(issue.severity, issue.severity)

    @api.depends(
        "description", "qty_expected", "qty_actual", "qty_affected",
        "value_affected", "value_difference", "diagnosis_data",
    )
    def _compute_diagnosis_summary_html(self):
        for issue in self:
            detected = []
            if issue.qty_actual or issue.qty_expected:
                detected.append(
                    _("On-hand / actual: %(qty)s") % {"qty": issue.qty_actual}
                )
                detected.append(
                    _("Valuation / expected: %(qty)s") % {"qty": issue.qty_expected}
                )
            if issue.qty_affected:
                detected.append(_("Difference: %(qty)s") % {"qty": issue.qty_affected})
            if issue.value_affected:
                detected.append(_("Value impact: %(val)s") % {"val": issue.value_affected})
            if issue.value_difference:
                detected.append(
                    _("Estimated value difference: %(val)s") % {"val": issue.value_difference}
                )
            if detected:
                issue.diagnosis_summary_html = (
                    "<ul>%s</ul>" % "".join("<li>%s</li>" % d for d in detected)
                )
            elif issue.description:
                issue.diagnosis_summary_html = "<p>%s</p>" % issue.description
            else:
                issue.diagnosis_summary_html = False

    @api.model
    def enrich_from_registry(self, vals):
        """Apply structured metadata defaults from the check registry."""
        check_type = vals.get("check_type")
        if not check_type:
            return vals
        meta = get_check_definition(check_type)
        if not meta:
            return vals
        vals.setdefault("check_category", meta.get("category"))
        vals.setdefault("resolution_type", meta.get("resolution_type"))
        vals.setdefault("why_it_matters", meta.get("why_it_matters"))
        if meta.get("possible_causes") and not vals.get("possible_causes"):
            vals["possible_causes"] = "\n".join(meta["possible_causes"])
        if meta.get("recommended_action") and not vals.get("recommendation"):
            vals["recommendation"] = meta["recommended_action"]
        return vals

    @api.model
    def _enrich_affected_records(self, vals):
        """Derive template / category / location / warehouse from product or move.

        Most checks only pass ``product_id`` (and optionally SVL / stock move).
        The Affected Records form expects the related master-data fields too.
        """
        vals = dict(vals)
        move = False

        if vals.get("product_id"):
            product = self.env["product.product"].browse(vals["product_id"])
            vals.setdefault("product_tmpl_id", product.product_tmpl_id.id)
            vals.setdefault("categ_id", product.categ_id.id)

        if vals.get("stock_move_id"):
            move = self.env["stock.move"].browse(vals["stock_move_id"])
            if not vals.get("product_id") and move.product_id:
                vals["product_id"] = move.product_id.id
                vals.setdefault("product_tmpl_id", move.product_id.product_tmpl_id.id)
                vals.setdefault("categ_id", move.product_id.categ_id.id)
            if not vals.get("location_id"):
                dest = move.location_dest_id
                src = move.location_id
                if dest and dest.usage == "internal":
                    vals["location_id"] = dest.id
                elif src and src.usage == "internal":
                    vals["location_id"] = src.id
                elif dest:
                    vals["location_id"] = dest.id

        if vals.get("svl_id"):
            svl = self.env["stock.valuation.layer"].browse(vals["svl_id"])
            if not vals.get("account_move_id") and svl.account_move_id:
                vals["account_move_id"] = svl.account_move_id.id
            if not vals.get("product_id") and svl.product_id:
                vals["product_id"] = svl.product_id.id
                vals.setdefault("product_tmpl_id", svl.product_id.product_tmpl_id.id)
                vals.setdefault("categ_id", svl.product_id.categ_id.id)

        if vals.get("location_id") and not vals.get("warehouse_id"):
            location = self.env["stock.location"].browse(vals["location_id"])
            company = move.company_id if move else False
            if not company and vals.get("scan_id"):
                company = self.env["est.inventory.doctor.scan"].browse(
                    vals["scan_id"]
                ).company_id
            warehouse = self.env["stock.warehouse"].search([
                ("view_location_id", "parent_of", location.id),
                ("company_id", "=", (company or self.env.company).id),
            ], limit=1)
            if warehouse:
                vals["warehouse_id"] = warehouse.id

        return vals

    @api.model_create_multi
    def create(self, vals_list):
        enriched = [
            self._enrich_affected_records(self.enrich_from_registry(dict(vals)))
            for vals in vals_list
        ]
        return super().create(enriched)

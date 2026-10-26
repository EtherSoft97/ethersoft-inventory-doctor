# -*- coding: utf-8 -*-
"""Structured metadata for Inventory Doctor diagnostic checks."""

from odoo import _

# Health score weights (documented, deterministic)
HEALTH_SCORE_WEIGHTS = {
    "critical": 12,
    "warning": 4,
    "info": 1,
}
HEALTH_SCORE_CAP = 100


def get_check_definitions():
    """Return maintainable metadata for all diagnostic checks."""
    return {
        "negative_stock": {
            "name": _("Negative Stock"),
            "category": "stock",
            "default_severity": "critical",
            "why_it_matters": _(
                "Negative on-hand quantity means Odoo believes more product left "
                "the warehouse than was available. This breaks reservation, FIFO "
                "consumption, and can produce incorrect valuation."
            ),
            "possible_causes": [
                _("Outgoing move validated before incoming quantity was recorded"),
                _("Incorrect inventory adjustment"),
                _("Manual database change"),
                _("Move processed in wrong order"),
            ],
            "recommended_action": _(
                "Review recent stock moves, perform a physical count, and "
                "create an inventory adjustment to correct the quantity."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "stock_without_valuation": {
            "name": _("Stock Without Valuation"),
            "category": "valuation",
            "default_severity": "critical",
            "why_it_matters": _(
                "Real-time valued products with physical stock should have "
                "valuation layers. Missing layers mean inventory value is not "
                "recorded in accounting."
            ),
            "possible_causes": [
                _("Valuation method was changed after moves were processed"),
                _("Incomplete stock move processing"),
                _("Manual data modification"),
            ],
            "recommended_action": _(
                "Run a stock valuation report and reprocess done moves or "
                "create a revaluation entry."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "valuation_discrepancy": {
            "name": _("Valuation Discrepancy"),
            "category": "valuation",
            "default_severity": "critical",
            "why_it_matters": _(
                "The physical quantity and stock valuation recorded in Odoo do "
                "not appear to be aligned. This can affect inventory valuation "
                "and accounting reporting."
            ),
            "possible_causes": [
                _("Incorrect inventory adjustment"),
                _("Missing valuation layer"),
                _("Incorrect stock move"),
                _("Manual data modification"),
                _("Costing configuration problem"),
            ],
            "recommended_action": _(
                "Review the related stock moves and valuation layers before "
                "applying a correction."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "guided",
        },
        "abnormal_cost": {
            "name": _("Abnormal Cost"),
            "category": "valuation",
            "default_severity": "warning",
            "why_it_matters": _(
                "Products with zero or extreme costs produce incorrect COGS, "
                "margins, and inventory valuation on reports."
            ),
            "possible_causes": [
                _("Standard price never set after go-live"),
                _("Manual cost reset"),
                _("Large revaluation not reflected in standard price"),
            ],
            "recommended_action": _(
                "Align the product cost with average valuation or last purchase price."
            ),
            "auto_fix": True,
            "fix_safety": "safe",
            "resolution_type": "auto_fix",
        },
        "fifo_inconsistency": {
            "name": _("FIFO Inconsistency"),
            "category": "valuation",
            "default_severity": "critical",
            "why_it_matters": _(
                "FIFO layers with negative remaining quantity or broken linkage "
                "cause incorrect cost consumption on deliveries."
            ),
            "possible_causes": [
                _("Over-consumption of incoming layers"),
                _("Negative stock event"),
                _("Manual layer correction"),
            ],
            "recommended_action": _(
                "Inspect linked stock moves chronologically and correct layers manually."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "guided",
        },
        "missing_journal_entry": {
            "name": _("Missing Journal Entry"),
            "category": "accounting",
            "default_severity": "warning",
            "why_it_matters": _(
                "Real-time inventory valuation should post accounting entries. "
                "Missing entries create a gap between stock and the general ledger."
            ),
            "possible_causes": [
                _("Stock accounts not configured at time of move"),
                _("Move posted before accounts were set"),
                _("Accounting lock or posting error"),
            ],
            "recommended_action": _(
                "Regenerate the journal entry when a stock move is linked; "
                "otherwise review the layer manually."
            ),
            "auto_fix": True,
            "fix_safety": "safe",
            "resolution_type": "auto_fix",
        },
        "orphaned_svl": {
            "name": _("Orphaned Valuation Layer"),
            "category": "valuation",
            "default_severity": "warning",
            "why_it_matters": _(
                "Valuation layers without stock move or parent linkage are "
                "difficult to reconcile and may indicate data corruption."
            ),
            "possible_causes": [
                _("Manual SQL correction"),
                _("Import without move linkage"),
                _("Cancelled move left layer behind"),
            ],
            "recommended_action": _(
                "Open the valuation layer, review its origin, and re-link or remove it."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "missing_inventory_account": {
            "name": _("Missing Inventory Account"),
            "category": "configuration",
            "default_severity": "warning",
            "why_it_matters": _(
                "Automated inventory valuation requires stock input, output, "
                "and valuation accounts on product categories."
            ),
            "possible_causes": [
                _("New category created without accounts"),
                _("Parent category accounts not inherited"),
            ],
            "recommended_action": _(
                "Configure accounts on the category or inherit from the parent category."
            ),
            "auto_fix": True,
            "fix_safety": "safe",
            "resolution_type": "auto_fix",
        },
        "inconsistent_cost_method": {
            "name": _("Inconsistent Costing Method"),
            "category": "configuration",
            "default_severity": "critical",
            "why_it_matters": _(
                "Without a costing method, Odoo cannot predictably value "
                "inventory movements for products in this category."
            ),
            "possible_causes": [
                _("Category created without costing defaults"),
                _("Property not set after module install"),
            ],
            "recommended_action": _(
                "Set FIFO, AVCO, or Standard costing on the product category."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "duplicate_product": {
            "name": _("Duplicate Product"),
            "category": "master_data",
            "default_severity": "warning",
            "why_it_matters": _(
                "Duplicate internal references or barcodes cause wrong product "
                "selection in sales, purchases, and integrations."
            ),
            "possible_causes": [
                _("Product duplicated instead of variant created"),
                _("Import created duplicates"),
            ],
            "recommended_action": _(
                "Merge duplicate products or assign unique references."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "missing_route": {
            "name": _("Missing Route"),
            "category": "configuration",
            "default_severity": "info",
            "why_it_matters": _(
                "Products without routes rely entirely on warehouse defaults. "
                "This may be fine for simple setups but limits advanced flows."
            ),
            "possible_causes": [
                _("Product created without route assignment"),
                _("Category has no default routes"),
            ],
            "recommended_action": _(
                "If you use MTO, dropship, or cross-dock, assign appropriate routes."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "warehouse_misconfig": {
            "name": _("Warehouse Misconfiguration"),
            "category": "configuration",
            "default_severity": "warning",
            "why_it_matters": _(
                "Warehouse locations drive reservation, picking routes, and "
                "valuation location flows."
            ),
            "possible_causes": [
                _("Warehouse created with incomplete location setup"),
                _("Delivery steps changed without location update"),
            ],
            "recommended_action": _(
                "Open warehouse settings and verify locations match delivery steps."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
        "negative_valuation": {
            "name": _("Negative Inventory Valuation"),
            "category": "valuation",
            "default_severity": "critical",
            "why_it_matters": _(
                "Negative inventory value breaks balance sheet reporting and "
                "COGS calculation."
            ),
            "possible_causes": [
                _("Over-consumption of valuation layers"),
                _("Negative stock combined with FIFO/AVCO"),
                _("Manual revaluation error"),
            ],
            "recommended_action": _(
                "Investigate valuation layers and post a revaluation adjustment."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "guided",
        },
        "suspicious_adjustment": {
            "name": _("Suspicious Stock Adjustment"),
            "category": "stock",
            "default_severity": "warning",
            "why_it_matters": _(
                "Large inventory adjustments may indicate data cleanup, testing, "
                "or uncorrected stock discrepancies."
            ),
            "possible_causes": [
                _("Bulk inventory correction"),
                _("Test data in production"),
                _("Migration adjustment"),
            ],
            "recommended_action": _(
                "Verify this adjustment was intentional and documented."
            ),
            "auto_fix": False,
            "fix_safety": "manual",
            "resolution_type": "manual",
        },
    }


def get_check_definition(check_type):
    return get_check_definitions().get(check_type, {})

{
    "name": "EtherSoft Inventory Doctor",
    "version": "17.0.3.1.2",
    "category": "Inventory/Inventory",
    "summary": "Free Inventory Diagnostic Scanner for Odoo 17",
    "description": """
EtherSoft Inventory Doctor (Free)
=================================

Scan your Odoo inventory and discover potential health and data integrity problems.

Free features:
- Inventory Health Dashboard
- Run real diagnostic scans (14 checks)
- Health Score and issue counts
- Top Diagnoses summary

Install EtherSoft Inventory Doctor PRO for detailed diagnosis,
root-cause analysis, Safe Auto-Fix, Fix Audit Log, and PDF reports.
    """,
    "author": "EtherSoft",
    "depends": [
        "mail",
        "stock_account",
        "purchase_stock",
    ],
    "data": [
        "security/inventory_doctor_security.xml",
        "security/ir.model.access.csv",
        "views/inventory_doctor_scan_views.xml",
        "views/inventory_doctor_menus.xml",
        "views/inventory_doctor_menu_icon.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ethersoft_inventory_doctor/static/src/dashboard/dashboard.js",
            "ethersoft_inventory_doctor/static/src/dashboard/dashboard.xml",
            "ethersoft_inventory_doctor/static/src/dashboard/dashboard.scss",
        ],
    },
    "license": "LGPL-3",
    "installable": True,
    "application": True,
}

{
    "name": "Inventory Doctor",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "Free inventory diagnostic scanner for stock, valuation, and configuration issues",
    "description": """
Inventory Doctor (Free)
=======================

Scan your Odoo inventory and discover potential health and data integrity problems.

Free features:
- Inventory Health Dashboard
- Run real diagnostic scans (14 checks)
- Health Score and issue counts
- Top Diagnoses summary

Install Inventory Doctor PRO for detailed diagnosis,
root-cause analysis, Safe Auto-Fix, Fix Audit Log, and PDF reports.
    """,
    "author": "EtherSoft",
    "website": "https://www.ethersoft.dev",
    "support": "support@ethersoft.dev",
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
    "images": [
        "static/description/banner.png",
        "static/description/screenshot_dashboard.png",
        "static/description/screenshot_dashboard_issues.png",
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

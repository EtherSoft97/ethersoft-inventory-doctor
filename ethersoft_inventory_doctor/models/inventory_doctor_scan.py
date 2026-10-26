# -*- coding: utf-8 -*-

import time

from odoo import _, api, fields, models, release
from odoo.exceptions import UserError

from .inventory_doctor_check_registry import HEALTH_SCORE_CAP, HEALTH_SCORE_WEIGHTS


class EstInventoryDoctorScan(models.Model):
    _name = "est.inventory.doctor.scan"
    _description = "Inventory Doctor Scan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "scan_date desc, id desc"

    name = fields.Char(
        required=True,
        default=lambda self: _("Inventory Health Scan"),
        tracking=True,
    )
    scan_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
        tracking=True,
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Scanned By",
        default=lambda self: self.env.user,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    odoo_version = fields.Char(readonly=True)
    duration_seconds = fields.Float(readonly=True, digits=(16, 2))
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    error_message = fields.Text(readonly=True)

    health_score = fields.Integer(readonly=True)
    health_score_label = fields.Char(readonly=True)
    health_score_breakdown = fields.Json(readonly=True)

    issue_ids = fields.One2many(
        "est.inventory.doctor.issue",
        "scan_id",
        string="Issues",
    )
    issue_count = fields.Integer(compute="_compute_issue_stats")
    critical_count = fields.Integer(compute="_compute_issue_stats")
    warning_count = fields.Integer(compute="_compute_issue_stats")
    info_count = fields.Integer(compute="_compute_issue_stats")
    open_count = fields.Integer(compute="_compute_issue_stats")
    fixable_count = fields.Integer(compute="_compute_issue_stats")
    fixed_count = fields.Integer(compute="_compute_issue_stats")

    # Frozen at scan completion so history cards keep original findings
    # after a newer scan supersedes old open issues to "ignored".
    snapshot_critical = fields.Integer(readonly=True)
    snapshot_warning = fields.Integer(readonly=True)
    snapshot_info = fields.Integer(readonly=True)
    snapshot_open = fields.Integer(readonly=True)
    snapshot_fixable = fields.Integer(readonly=True)
    snapshot_issue_count = fields.Integer(readonly=True)

    summary_html = fields.Html(compute="_compute_summary_html", sanitize=False)
    health_score_explanation_html = fields.Html(
        compute="_compute_health_score_explanation_html",
        sanitize=False,
    )

    @api.depends("issue_ids", "issue_ids.severity", "issue_ids.state", "issue_ids.fix_available", "issue_ids.fix_safe")
    def _compute_issue_stats(self):
        for scan in self:
            issues = scan.issue_ids
            open_like = issues.filtered(lambda i: i.state in ("open", "in_progress"))
            scan.issue_count = len(issues)
            scan.critical_count = len(open_like.filtered(lambda i: i.severity == "critical"))
            scan.warning_count = len(open_like.filtered(lambda i: i.severity == "warning"))
            scan.info_count = len(open_like.filtered(lambda i: i.severity == "info"))
            scan.open_count = len(issues.filtered(lambda i: i.state == "open"))
            scan.fixable_count = len(
                open_like.filtered(lambda i: i.fix_available and i.fix_safe)
            )
            scan.fixed_count = len(issues.filtered(lambda i: i.state == "fixed"))

    def _capture_issue_snapshot(self):
        """Freeze issue counts as they were when this scan finished."""
        for scan in self:
            issues = scan.issue_ids
            open_like = issues.filtered(lambda i: i.state in ("open", "in_progress"))
            scan.write({
                "snapshot_issue_count": len(issues),
                "snapshot_critical": len(open_like.filtered(lambda i: i.severity == "critical")),
                "snapshot_warning": len(open_like.filtered(lambda i: i.severity == "warning")),
                "snapshot_info": len(open_like.filtered(lambda i: i.severity == "info")),
                "snapshot_open": len(issues.filtered(lambda i: i.state == "open")),
                "snapshot_fixable": len(
                    open_like.filtered(lambda i: i.fix_available and i.fix_safe)
                ),
            })

    def get_history_stats(self):
        """Counts for historical cards: prefer snapshot; else rebuild from issues."""
        self.ensure_one()
        if (
            self.snapshot_issue_count
            or self.snapshot_open
            or self.snapshot_critical
            or self.snapshot_warning
            or self.snapshot_info
        ):
            return {
                "issue_count": self.snapshot_issue_count,
                "critical_count": self.snapshot_critical,
                "warning_count": self.snapshot_warning,
                "info_count": self.snapshot_info,
                "open_count": self.snapshot_open,
                "fixable_count": self.snapshot_fixable,
                "fixed_count": self.fixed_count,
            }
        # Older scans (before snapshot fields): use all issues found on this scan
        issues = self.issue_ids
        return {
            "issue_count": len(issues),
            "critical_count": len(issues.filtered(lambda i: i.severity == "critical")),
            "warning_count": len(issues.filtered(lambda i: i.severity == "warning")),
            "info_count": len(issues.filtered(lambda i: i.severity == "info")),
            "open_count": len(issues),
            "fixable_count": len(
                issues.filtered(lambda i: i.fix_available and i.fix_safe)
            ),
            "fixed_count": len(issues.filtered(lambda i: i.state == "fixed")),
        }

    @api.depends("health_score_breakdown", "health_score", "health_score_label")
    def _compute_health_score_explanation_html(self):
        for scan in self:
            breakdown = scan.health_score_breakdown or {}
            if not breakdown:
                scan.health_score_explanation_html = (
                    "<p class='text-muted'>Run a scan to calculate the health score.</p>"
                )
                continue
            rows = []
            count_map = {
                "critical_penalty": "critical_count",
                "warning_penalty": "warning_count",
                "info_penalty": "info_count",
            }
            for key, label in [
                ("critical_penalty", _("Critical issues")),
                ("warning_penalty", _("Warnings")),
                ("info_penalty", _("Informational")),
            ]:
                val = breakdown.get(key, 0)
                if val:
                    count = breakdown.get(count_map[key], 0)
                    rows.append("<li>%s: %s × points = −%s</li>" % (label, count, val))
            scan.health_score_explanation_html = (
                "<div class='o_est_score_explain'>"
                "<p><strong>%s: %s/100 (%s)</strong></p>"
                "<p>%s</p>"
                "<ul class='mb-2'>%s</ul>"
                "<p class='text-muted mb-0'>%s</p>"
                "</div>"
            ) % (
                _("Health Score"),
                scan.health_score,
                scan.health_score_label or "",
                _("The score starts at 100. Each open issue subtracts points. "
                  "If the total penalty is 100 or more, the score is shown as 0."),
                "".join(rows) or "<li>%s</li>" % _("No open issues"),
                _("Each critical issue subtracts %(crit)s points. "
                  "Each warning subtracts %(warn)s points. "
                  "Each info issue subtracts %(info)s point.") % {
                    "crit": HEALTH_SCORE_WEIGHTS["critical"],
                    "warn": HEALTH_SCORE_WEIGHTS["warning"],
                    "info": HEALTH_SCORE_WEIGHTS["info"],
                },
            )

    @api.depends(
        "issue_ids",
        "issue_ids.check_type",
        "issue_ids.severity",
        "issue_ids.state",
    )
    def _compute_summary_html(self):
        check_labels = dict(self.env["est.inventory.doctor.issue"]._selection_check_type())
        for scan in self:
            if not scan.issue_ids:
                scan.summary_html = (
                    '<p class="text-muted">No issues found — inventory looks healthy.</p>'
                )
                continue

            grouped = {}
            for issue in scan.issue_ids.filtered(lambda i: i.state == "open"):
                key = issue.check_type
                grouped.setdefault(key, {"count": 0, "severity": issue.severity})
                grouped[key]["count"] += 1
                if issue.severity == "critical":
                    grouped[key]["severity"] = "critical"
                elif issue.severity == "warning" and grouped[key]["severity"] != "critical":
                    grouped[key]["severity"] = "warning"

            rows = []
            for check_type, data in sorted(
                grouped.items(),
                key=lambda x: (-{"critical": 3, "warning": 2, "info": 1}[x[1]["severity"]], x[0]),
            ):
                label = check_labels.get(check_type, check_type)
                rows.append(
                    "<li><strong>%s</strong> %s (%s)</li>"
                    % (data["count"], label, data["severity"])
                )
            scan.summary_html = '<ul class="mb-0">%s</ul>' % "".join(rows)

    def get_report_findings(self):
        """Grouped open-issue counts for the PDF report (no emoji)."""
        self.ensure_one()
        check_labels = dict(self.env["est.inventory.doctor.issue"]._selection_check_type())
        grouped = {}
        for issue in self.issue_ids.filtered(lambda i: i.state == "open"):
            bucket = grouped.setdefault(
                issue.check_type,
                {"check_type": issue.check_type, "count": 0, "severity": issue.severity},
            )
            bucket["count"] += 1
            if issue.severity == "critical":
                bucket["severity"] = "critical"
            elif issue.severity == "warning" and bucket["severity"] != "critical":
                bucket["severity"] = "warning"
        findings = []
        for check_type, data in sorted(
            grouped.items(),
            key=lambda x: (-{"critical": 3, "warning": 2, "info": 1}[x[1]["severity"]], x[0]),
        ):
            findings.append({
                "label": check_labels.get(check_type, check_type),
                "count": data["count"],
                "severity": data["severity"].capitalize(),
            })
        return findings

    @api.model
    def _compute_health_score_values(self, open_issues):
        critical = len(open_issues.filtered(lambda i: i.severity == "critical"))
        warning = len(open_issues.filtered(lambda i: i.severity == "warning"))
        info = len(open_issues.filtered(lambda i: i.severity == "info"))
        critical_penalty = critical * HEALTH_SCORE_WEIGHTS["critical"]
        warning_penalty = warning * HEALTH_SCORE_WEIGHTS["warning"]
        info_penalty = info * HEALTH_SCORE_WEIGHTS["info"]
        total_penalty = min(
            critical_penalty + warning_penalty + info_penalty,
            HEALTH_SCORE_CAP,
        )
        score = max(0, HEALTH_SCORE_CAP - total_penalty)
        if score >= 85:
            label = _("Healthy")
            tone = "good"
        elif score >= 60:
            label = _("Needs Attention")
            tone = "warn"
        else:
            label = _("Critical")
            tone = "bad"
        return {
            "health_score": score,
            "health_label": label,
            "health_tone": tone,
            "breakdown": {
                "critical_count": critical,
                "warning_count": warning,
                "info_count": info,
                "critical_penalty": critical_penalty,
                "warning_penalty": warning_penalty,
                "info_penalty": info_penalty,
                "total_penalty": total_penalty,
            },
        }

    def _supersede_previous_scan_issues(self):
        Issue = self.env["est.inventory.doctor.issue"]
        for scan in self:
            stale_issues = Issue.search([
                ("company_id", "=", scan.company_id.id),
                ("scan_id", "!=", scan.id),
                ("state", "in", ("open", "in_progress")),
            ])
            if stale_issues:
                stale_issues.write({"state": "ignored"})

    def action_run_scan(self):
        self.ensure_one()
        if self.state == "running":
            raise UserError(_("A scan is already running."))
        running = self.search([
            ("company_id", "=", self.company_id.id),
            ("state", "=", "running"),
            ("id", "!=", self.id),
        ], limit=1)
        if running:
            raise UserError(_("Another scan is already running for this company."))
        started = time.time()
        self.write({
            "state": "running",
            "error_message": False,
            "user_id": self.env.uid,
            "odoo_version": release.version,
        })
        self.issue_ids.unlink()
        try:
            self.env["est.inventory.doctor.checker"].run_scan(self)
            open_issues = self.issue_ids.filtered(lambda i: i.state == "open")
            score_vals = self._compute_health_score_values(open_issues)
            duration = round(time.time() - started, 2)
            self.write({
                "state": "done",
                "duration_seconds": duration,
                "health_score": score_vals["health_score"],
                "health_score_label": score_vals["health_label"],
                "health_score_breakdown": score_vals["breakdown"],
            })
            self._capture_issue_snapshot()
            self._supersede_previous_scan_issues()
        except Exception as exc:
            self.write({
                "state": "error",
                "error_message": str(exc),
                "duration_seconds": round(time.time() - started, 2),
            })
            raise
        return True

    @api.model
    def _default_scan_name(self):
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return _("Inventory Health Scan — %s") % now.strftime("%Y-%m-%d %H:%M")

    @api.model
    def action_new_scan(self):
        running = self.search([
            ("company_id", "=", self.env.company.id),
            ("state", "=", "running"),
        ], limit=1)
        if running:
            raise UserError(_("A scan is already running. Please wait for it to finish."))
        scan = self.create({
            "name": self._default_scan_name(),
            "scan_date": fields.Datetime.now(),
            "user_id": self.env.uid,
        })
        scan.action_run_scan()
        return {
            "type": "ir.actions.act_window",
            "res_model": "est.inventory.doctor.scan",
            "view_mode": "form",
            "res_id": scan.id,
        }

    @api.model
    def get_dashboard_data(self):
        company = self.env.company
        Issue = self.env["est.inventory.doctor.issue"]
        Scan = self.env["est.inventory.doctor.scan"]

        company_domain = [("company_id", "=", company.id)]
        latest = Scan.search(company_domain, limit=1)

        if latest:
            open_issues = latest.issue_ids.filtered(lambda i: i.state == "open")
        else:
            open_issues = Issue.browse()
        check_labels = dict(Issue._selection_check_type())

        by_type = {}
        for issue in open_issues:
            bucket = by_type.setdefault(
                issue.check_type,
                {
                    "key": issue.check_type,
                    "label": check_labels.get(issue.check_type, issue.check_type),
                    "count": 0,
                    "severity": issue.severity,
                },
            )
            bucket["count"] += 1
            if issue.severity == "critical":
                bucket["severity"] = "critical"
            elif issue.severity == "warning" and bucket["severity"] != "critical":
                bucket["severity"] = "warning"

        type_breakdown = sorted(by_type.values(), key=lambda r: (-r["count"], r["label"]))
        max_type = max((r["count"] for r in type_breakdown), default=1)
        for row in type_breakdown:
            row["pct"] = round(100.0 * row["count"] / max_type) if max_type else 0

        score_vals = self._compute_health_score_values(open_issues)
        breakdown = score_vals["breakdown"]
        critical = breakdown["critical_count"]
        warning = breakdown["warning_count"]
        info = breakdown["info_count"]
        open_count = len(open_issues)
        fixable = len(open_issues.filtered(lambda i: i.fix_available and i.fix_safe))
        # Count every resolved issue for this company, not only the latest scan.
        # A new scan creates fresh open issues, so latest.scan fixed_count is often 0.
        fixed = Issue.search_count(company_domain + [("state", "=", "fixed")])
        scan_count = Scan.search_count(company_domain)

        recent_scans = []
        for scan in Scan.search(company_domain, limit=8):
            stats = scan.get_history_stats()
            recent_scans.append({
                "id": scan.id,
                "name": scan.name,
                "scan_date": fields.Datetime.to_string(scan.scan_date) if scan.scan_date else False,
                "scan_date_display": fields.Datetime.context_timestamp(
                    self, scan.scan_date
                ).strftime("%b %d, %Y %H:%M") if scan.scan_date else "",
                "user_name": scan.user_id.display_name if scan.user_id else "",
                "state": scan.state,
                "health_score": scan.health_score,
                "issue_count": stats["issue_count"],
                "critical_count": stats["critical_count"],
                "warning_count": stats["warning_count"],
                "open_count": stats["open_count"],
                "fixable_count": stats["fixable_count"],
                "fixed_count": stats["fixed_count"],
                "duration_seconds": scan.duration_seconds,
            })

        severity_total = max(critical + warning + info, 1)
        latest_display = ""
        if latest and latest.scan_date:
            latest_display = fields.Datetime.context_timestamp(
                self, latest.scan_date
            ).strftime("%b %d, %Y %H:%M")

        return {
            "company_name": company.display_name,
            "health_score": score_vals["health_score"],
            "health_label": score_vals["health_label"],
            "health_tone": score_vals["health_tone"],
            "health_breakdown": breakdown,
            "kpis": {
                "open": open_count,
                "critical": critical,
                "warning": warning,
                "info": info,
                "fixable": fixable,
                "fixed": fixed,
                "scans": scan_count,
            },
            "severity_chart": {
                "critical": critical,
                "warning": warning,
                "info": info,
                "critical_pct": round(100.0 * critical / severity_total),
                "warning_pct": round(100.0 * warning / severity_total),
                "info_pct": round(100.0 * info / severity_total),
            },
            "type_breakdown": type_breakdown[:10],
            "recent_scans": recent_scans,
            "latest_scan_id": latest.id if latest else False,
            "latest_scan_date": latest_display,
        }

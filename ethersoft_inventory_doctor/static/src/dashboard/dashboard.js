/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/**
 * Free Inventory Doctor dashboard.
 * Drill-down into issues / auto-fix is unlocked by the PRO module patch.
 */
export class InventoryDoctorDashboard extends Component {
    static template = "ethersoft_inventory_doctor.Dashboard";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            running: false,
            loaded: false,
            showScoreHelp: false,
            data: {
                company_name: "",
                health_score: 100,
                health_label: "",
                health_tone: "good",
                health_breakdown: {},
                kpis: {},
                severity_chart: {},
                type_breakdown: [],
                recent_scans: [],
            },
        });
        onWillStart(() => this.loadData());
    }

    /** Free build: always false. PRO patches this getter. */
    get isPro() {
        return false;
    }

    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "est.inventory.doctor.scan",
                "get_dashboard_data",
                []
            );
            this.state.loaded = true;
        } finally {
            this.state.loading = false;
        }
    }

    get donutStyle() {
        const c = this.state.data.severity_chart || {};
        const crit = c.critical_pct || 0;
        const warn = c.warning_pct || 0;
        if (!(c.critical || c.warning || c.info)) {
            return "background: conic-gradient(#34d399 0 100%);";
        }
        const a = crit;
        const b = crit + warn;
        return `background: conic-gradient(#ef4444 0 ${a}%, #f59e0b ${a}% ${b}%, #38bdf8 ${b}% 100%);`;
    }

    get scoreBreakdownText() {
        const b = this.state.data.health_breakdown || {};
        const parts = [];
        if (b.critical_penalty) {
            parts.push(_t("Critical issues: -%s", b.critical_penalty));
        }
        if (b.warning_penalty) {
            parts.push(_t("Warnings: -%s", b.warning_penalty));
        }
        if (b.info_penalty) {
            parts.push(_t("Informational: -%s", b.info_penalty));
        }
        return parts.join(" · ") || _t("No open issue penalties.");
    }

    formatCount(value) {
        return Number(value || 0).toLocaleString();
    }

    get statusTone() {
        const kpis = this.state.data.kpis || {};
        if ((kpis.critical || 0) > 0) {
            return "critical";
        }
        if ((kpis.open || 0) > 0) {
            return "warn";
        }
        return "good";
    }

    get statusMessage() {
        const kpis = this.state.data.kpis || {};
        const critical = kpis.critical || 0;
        const open = kpis.open || 0;
        if (critical > 0) {
            return _t("Attention required · %s critical issues", this.formatCount(critical));
        }
        if (open > 0) {
            return _t("Attention required · %s open issues", this.formatCount(open));
        }
        return _t("Inventory looks healthy · No critical issues detected");
    }

    toggleScoreHelp() {
        this.state.showScoreHelp = !this.state.showScoreHelp;
    }

    notifyPro(feature) {
        this.notification.add(
            _t("%s\nAvailable in EtherSoft Inventory Doctor PRO.", feature),
            { type: "warning", sticky: false }
        );
    }

    async onRunScan() {
        if (this.state.running) {
            return;
        }
        this.state.running = true;
        try {
            await this.orm.call("est.inventory.doctor.scan", "action_new_scan", []);
            this.notification.add(_t("Scan completed"), { type: "success" });
            await this.loadData();
        } catch (error) {
            this.notification.add(_t("Scan failed — open Scan History for details."), {
                type: "danger",
            });
            await this.loadData();
        } finally {
            this.state.running = false;
        }
    }

    onOpenIssues(domainExtra = []) {
        return this.notifyPro(_t("Detailed Issue Evidence"));
    }

    onOpenAllIssues() {
        return this.notifyPro(_t("Detailed Issue Evidence"));
    }

    onOpenCritical() {
        return this.onOpenIssues([["severity", "=", "critical"]]);
    }

    onOpenWarning() {
        return this.onOpenIssues([["severity", "=", "warning"]]);
    }

    onOpenFixable() {
        return this.notifyPro(_t("Safe Auto-Fix"));
    }

    onOpenFixed() {
        return this.notifyPro(_t("Detailed Issue Evidence"));
    }

    onOpenScans() {
        return this.action.doAction("ethersoft_inventory_doctor.action_est_inventory_doctor_scan");
    }

    onOpenScan(scanId) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "est.inventory.doctor.scan",
            views: [[false, "form"]],
            res_id: scanId,
        });
    }

    onOpenCheckType(checkType) {
        return this.notifyPro(_t("Advanced Diagnosis"));
    }

    stateClass(state) {
        return {
            done: "est-pill est-pill--success",
            running: "est-pill est-pill--warn",
            error: "est-pill est-pill--danger",
            draft: "est-pill est-pill--muted",
        }[state] || "est-pill";
    }
}

registry.category("actions").add("est_inventory_doctor_dashboard", InventoryDoctorDashboard);

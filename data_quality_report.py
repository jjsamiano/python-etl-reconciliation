"""
data_quality_report.py

Reads the output of reconcile.py and produces a markdown data
quality report -- the kind of one-pager that would sit behind a
KPI dashboard for a weekly ops/data quality review.

Run reconcile.py first:
    python3 reconcile.py && python3 data_quality_report.py
"""

import csv
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def read_csv_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    master = read_csv_rows(os.path.join(OUT_DIR, "reconciled_master.csv"))
    discrepancies = read_csv_rows(os.path.join(OUT_DIR, "discrepancy_report.csv"))
    summary_rows = read_csv_rows(os.path.join(OUT_DIR, "match_summary.csv"))
    summary = {row["metric"]: int(row["count"]) for row in summary_rows}

    total = len(master)
    has_email = sum(1 for r in master if r["email"])
    has_phone = sum(1 for r in master if r["phone"])
    has_company = sum(1 for r in master if r["company"])
    matched_pct = round(100 * summary["matched_across_systems"] / summary["crm_records_after_dedup"], 1)

    raw_records = summary["crm_records_raw"] + summary["marketing_records_raw"]
    redundancy_removed = summary["intra_crm_duplicates_resolved"]
    redundancy_pct = round(100 * redundancy_removed / summary["crm_records_raw"], 1)

    lines = []
    lines.append("# Data Quality & Reconciliation Report\n")
    lines.append("Synthetic demo dataset -- CRM export reconciled against a marketing platform export.\n")

    lines.append("## Summary KPIs\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Unique contacts after reconciliation | {total} |")
    lines.append(f"| CRM duplicate records resolved (fuzzy pass) | {redundancy_removed} ({redundancy_pct}% of raw CRM rows) |")
    lines.append(f"| Cross-system match rate (CRM matched to Marketing) | {matched_pct}% |")
    lines.append(f"| Records found in only one system | {summary['crm_only'] + summary['marketing_only']} |")
    lines.append(f"| Field-level discrepancies flagged for review | {summary['discrepancies_flagged']} |")
    lines.append("")

    lines.append("## Field completeness (post-reconciliation)\n")
    lines.append("| Field | % Complete |")
    lines.append("|---|---|")
    lines.append(f"| Email | {round(100 * has_email / total, 1)}% |")
    lines.append(f"| Phone | {round(100 * has_phone / total, 1)}% |")
    lines.append(f"| Company | {round(100 * has_company / total, 1)}% |")
    lines.append("")

    lines.append("## Discrepancies flagged for manual review\n")
    if discrepancies:
        lines.append("| Person | Field | CRM Value | Marketing Value |")
        lines.append("|---|---|---|---|")
        for d in discrepancies:
            lines.append(f"| {d['person']} | {d['field']} | {d['crm_value']} | {d['marketing_value']} |")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Pipeline stages\n")
    lines.append(f"1. **SQL deterministic dedup** (see `01-sql-data-quality-toolkit`): 15 raw rows -> {summary['crm_records_raw']} after exact email/phone matching.")
    lines.append(f"2. **Python fuzzy dedup**: {summary['crm_records_raw']} -> {summary['crm_records_after_dedup']} after resolving name-spelling and malformed-email near-duplicates.")
    lines.append(f"3. **Cross-system reconciliation**: {summary['crm_records_after_dedup']} CRM contacts matched against {summary['marketing_records_raw']} marketing platform records -> {total} unique people, {summary['discrepancies_flagged']} discrepancies flagged rather than silently overwritten.")
    lines.append("")

    report_text = "\n".join(lines)
    out_path = os.path.join(OUT_DIR, "data_quality_report.md")
    with open(out_path, "w") as f:
        f.write(report_text)

    print(report_text)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()

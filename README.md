# Python ETL & Cross-System Reconciliation

Picks up where SQL-only deduplication hits its limit, and reconciles records
across two systems that don't share a common ID — the same shape of problem
as reconciling a Salesforce CRM against a legacy system, or a CRM against a
marketing automation platform, both of which I've done in production.

**Note on data:** both source files in `sample_data/` are synthetic. No
employer or client data is used anywhere in this repo.

## Pipeline

**Stage 1 — intra-CRM fuzzy dedup.** The CRM export has already been through
a SQL pass (see [`01-sql-data-quality-toolkit`](https://github.com/jjsamiano/sql-data-quality-toolkit)
that catches exact email/phone duplicates. Two near-duplicate pairs survive
that pass on purpose — a name-spelling variant ("John" vs "Jon" Reyes) and a
malformed-email record that shares a phone number with its clean duplicate
(Sarah Lopez). This stage resolves both using name-similarity scoring plus
shared secondary keys.

**Stage 2 — cross-system reconciliation.** The cleaned CRM contact list is
matched against a marketing platform export using a tiered strategy — exact
email, then exact phone, then fuzzy name — merged into a single master
record, and any field-level disagreement between the two sources (a
different phone number, a corrected email) is **flagged for review instead
of being silently overwritten** in either direction.

**Stage 3 — reporting.** `data_quality_report.py` turns the reconciliation
output into the summary KPIs (match rate, completeness, duplicates
resolved, discrepancies flagged) that would normally feed a Power BI or
Tableau dashboard — see [`03-kpi-dashboard-case-study`](https://github.com/jjsamiano/kpi-dashboard-case-study)
for that layer.

## Run it

```bash
python3 reconcile.py            # writes output/reconciled_master.csv,
                                 # discrepancy_report.csv, match_summary.csv
python3 data_quality_report.py  # writes output/data_quality_report.md
```

No dependencies outside the Python standard library.

## Result on the sample data

- 11 raw CRM records → **9 unique contacts** after fuzzy dedup
- 9 CRM contacts matched against 10 marketing platform records → **8 matched,
  1 CRM-only, 2 marketing-only** → 11 unique people total
- **3 field-level discrepancies** flagged (2 email, 1 phone) rather than
  auto-resolved, since picking the "correct" value on a conflict is a
  judgment call that belongs with whoever owns the data, not with the script

In a live environment, this reconciliation logic is exactly what I'd wire up
as a scheduled **n8n or Make workflow** — triggered on a new marketing
platform export, syncing matched/flagged results back into the CRM via its
REST API, with the discrepancy report routed to the data owner for review
instead of getting silently overwritten.

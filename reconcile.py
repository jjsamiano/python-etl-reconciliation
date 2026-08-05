"""
reconcile.py

Two-stage reconciliation pipeline:

  Stage 1 (intra-system fuzzy dedup): the CRM export
  (source_a_crm_export.csv) has already been through a SQL
  deduplication pass (see the companion sql-data-quality-toolkit
  project) that catches exact email/phone duplicates. Two
  near-duplicate pairs survived that pass because they don't share
  an exact key -- this stage resolves them with fuzzy name matching.

  Stage 2 (cross-system reconciliation): match the cleaned CRM
  contacts against a second system -- a marketing automation
  platform export -- using a tiered strategy (exact email, exact
  phone, fuzzy name+company), merge matched records, and flag
  field-level conflicts for review instead of silently overwriting
  either side.

Run:
    python3 reconcile.py

Outputs (written to output/):
    reconciled_master.csv   - one row per unique person, merged fields
    discrepancy_report.csv  - field-level conflicts between matched records
    match_summary.csv       - crm_only / marketing_only / matched breakdown
"""

import csv
import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "sample_data")
OUT_DIR = os.path.join(os.path.dirname(__file__), "output")


# ---------- normalization helpers ----------

def norm_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def norm_phone(phone: Optional[str]) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]


def norm_company(company: Optional[str]) -> str:
    c = (company or "").strip().lower()
    for suffix in (" inc", " llc", " corp", ".", ","):
        c = c.replace(suffix, "")
    return c.strip()


def name_similarity(name_a: str, name_b: str) -> float:
    return SequenceMatcher(None, name_a.lower(), name_b.lower()).ratio()


def is_valid_email(email: Optional[str]) -> bool:
    e = (email or "").strip()
    if "@" not in e:
        return False
    local, _, domain = e.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


FUZZY_NAME_THRESHOLD = 0.75


# ---------- data loading ----------

@dataclass
class Contact:
    source: str                # "crm" or "marketing"
    source_id: str
    first_name: str
    last_name: str
    email: str
    phone: str
    company: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def load_crm(path: str) -> list[Contact]:
    contacts = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            contacts.append(
                Contact(
                    source="crm",
                    source_id=row["crm_id"],
                    first_name=row["first_name"] or "",
                    last_name=row["last_name"] or "",
                    email=row["email"] or "",
                    phone=row["phone"] or "",
                    company=row["company"] or "",
                    extra={"state": row["state"], "lifecycle_stage": row["lifecycle_stage"]},
                )
            )
    return contacts


def load_marketing(path: str) -> list[Contact]:
    contacts = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            name_parts = row["full_name"].split(" ", 1)
            first = name_parts[0]
            last = name_parts[1] if len(name_parts) > 1 else ""
            contacts.append(
                Contact(
                    source="marketing",
                    source_id=row["marketing_id"],
                    first_name=first,
                    last_name=last,
                    email=row["email"] or "",
                    phone=row["phone"] or "",
                    company="",
                    extra={
                        "campaign_source": row["campaign_source"],
                        "last_campaign": row["last_campaign"],
                        "email_opens_30d": row["email_opens_30d"],
                    },
                )
            )
    return contacts


# ---------- stage 1: intra-CRM fuzzy dedup ----------

def dedup_crm(contacts: list[Contact]) -> tuple[list[Contact], list[tuple[Contact, Contact]]]:
    """Collapse near-duplicate CRM records that share a phone number
    OR a highly similar name + matching company, since the upstream
    SQL pass already removed exact email/phone duplicates."""
    merged_pairs: list[tuple[Contact, Contact]] = []
    survivors: list[Contact] = []
    consumed: set[str] = set()

    for i, a in enumerate(contacts):
        if a.source_id in consumed:
            continue
        cluster = [a]
        for b in contacts[i + 1:]:
            if b.source_id in consumed:
                continue
            same_phone = norm_phone(a.phone) and norm_phone(a.phone) == norm_phone(b.phone)
            similar_name = (
                name_similarity(a.first_name, b.first_name) >= FUZZY_NAME_THRESHOLD
                and a.last_name.lower() == b.last_name.lower()
                and norm_company(a.company) == norm_company(b.company)
            )
            if same_phone or similar_name:
                cluster.append(b)
                consumed.add(b.source_id)
        # survivor = most complete record (has phone, has company)
        cluster.sort(key=lambda c: (bool(norm_phone(c.phone)), bool(c.company)), reverse=True)
        winner, *losers = cluster
        survivors.append(winner)
        for loser in losers:
            merged_pairs.append((loser, winner))
        consumed.add(a.source_id)

    return survivors, merged_pairs


# ---------- stage 2: cross-system matching ----------

def match_cross_system(crm: list[Contact], marketing: list[Contact]):
    matched: list[tuple[Contact, Contact, str]] = []
    matched_marketing_ids: set[str] = set()

    for c in crm:
        best_match = None
        match_tier = None

        for m in marketing:
            if m.source_id in matched_marketing_ids:
                continue
            if norm_email(c.email) and norm_email(c.email) == norm_email(m.email):
                best_match, match_tier = m, "email"
                break
            if norm_phone(c.phone) and norm_phone(c.phone) == norm_phone(m.phone):
                best_match, match_tier = m, "phone"
                break

        if best_match is None:
            for m in marketing:
                if m.source_id in matched_marketing_ids:
                    continue
                if name_similarity(c.full_name, m.full_name) >= FUZZY_NAME_THRESHOLD:
                    best_match, match_tier = m, "fuzzy_name"
                    break

        if best_match:
            matched.append((c, best_match, match_tier))
            matched_marketing_ids.add(best_match.source_id)

    matched_crm_ids = {c.source_id for c, _, _ in matched}
    crm_only = [c for c in crm if c.source_id not in matched_crm_ids]
    marketing_only = [m for m in marketing if m.source_id not in matched_marketing_ids]

    return matched, crm_only, marketing_only


# ---------- output ----------

def best_email(crm: Optional[Contact], mkt: Optional[Contact]) -> str:
    if crm and is_valid_email(crm.email):
        return crm.email
    if mkt and is_valid_email(mkt.email):
        return mkt.email
    return (crm.email if crm and crm.email else (mkt.email if mkt else ""))


def build_master_row(crm: Optional[Contact], mkt: Optional[Contact], match_tier: str):
    row = {
        "full_name": (crm or mkt).full_name,
        "email": best_email(crm, mkt),
        "phone": (crm.phone if crm and norm_phone(crm.phone) else (mkt.phone if mkt else "")),
        "company": crm.company if crm else "",
        "lifecycle_stage": crm.extra.get("lifecycle_stage", "") if crm else "Marketing Lead",
        "campaign_source": mkt.extra.get("campaign_source", "") if mkt else "",
        "email_opens_30d": mkt.extra.get("email_opens_30d", "") if mkt else "",
        "match_status": match_tier,
    }
    return row


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    crm_raw = load_crm(os.path.join(DATA_DIR, "source_a_crm_export.csv"))
    marketing = load_marketing(os.path.join(DATA_DIR, "source_b_marketing_platform.csv"))

    crm_deduped, intra_merges = dedup_crm(crm_raw)
    matched, crm_only, marketing_only = match_cross_system(crm_deduped, marketing)

    master_rows = []
    discrepancies = []

    for c, m, tier in matched:
        master_rows.append(build_master_row(c, m, f"matched:{tier}"))
        if norm_phone(c.phone) and norm_phone(m.phone) and norm_phone(c.phone) != norm_phone(m.phone):
            discrepancies.append(
                {"person": c.full_name, "field": "phone", "crm_value": c.phone, "marketing_value": m.phone}
            )
        if not norm_phone(c.phone) and norm_phone(m.phone):
            discrepancies.append(
                {
                    "person": c.full_name,
                    "field": "phone",
                    "crm_value": c.phone or "(missing/invalid)",
                    "marketing_value": m.phone,
                }
            )
        if tier != "email" and norm_email(c.email) and norm_email(m.email) and norm_email(c.email) != norm_email(m.email):
            discrepancies.append(
                {
                    "person": c.full_name,
                    "field": "email",
                    "crm_value": c.email if is_valid_email(c.email) else f"{c.email} (malformed)",
                    "marketing_value": m.email,
                }
            )

    for c in crm_only:
        master_rows.append(build_master_row(c, None, "crm_only"))
    for m in marketing_only:
        master_rows.append(build_master_row(None, m, "marketing_only"))

    # write reconciled master
    with open(os.path.join(OUT_DIR, "reconciled_master.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(master_rows[0].keys()))
        writer.writeheader()
        writer.writerows(master_rows)

    # write discrepancy report
    with open(os.path.join(OUT_DIR, "discrepancy_report.csv"), "w", newline="") as f:
        fieldnames = ["person", "field", "crm_value", "marketing_value"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(discrepancies)

    # write match summary
    with open(os.path.join(OUT_DIR, "match_summary.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "count"])
        writer.writerow(["crm_records_raw", len(crm_raw)])
        writer.writerow(["intra_crm_duplicates_resolved", len(intra_merges)])
        writer.writerow(["crm_records_after_dedup", len(crm_deduped)])
        writer.writerow(["marketing_records_raw", len(marketing)])
        writer.writerow(["matched_across_systems", len(matched)])
        writer.writerow(["crm_only", len(crm_only)])
        writer.writerow(["marketing_only", len(marketing_only)])
        writer.writerow(["unique_people_total", len(master_rows)])
        writer.writerow(["discrepancies_flagged", len(discrepancies)])

    # console summary
    print("=== Stage 1: intra-CRM fuzzy dedup ===")
    for loser, winner in intra_merges:
        print(f"  merged '{loser.full_name}' ({loser.source_id}) -> '{winner.full_name}' ({winner.source_id})")
    print(f"  CRM: {len(crm_raw)} raw -> {len(crm_deduped)} unique\n")

    print("=== Stage 2: cross-system reconciliation ===")
    for c, m, tier in matched:
        print(f"  matched [{tier:10s}] CRM '{c.full_name}' <-> Marketing '{m.full_name}'")
    print(f"  crm_only: {len(crm_only)}   marketing_only: {len(marketing_only)}   matched: {len(matched)}")
    print(f"  total unique people across both systems: {len(master_rows)}\n")

    print(f"Discrepancies flagged for review: {len(discrepancies)}")
    for d in discrepancies:
        print(f"  {d['person']}: {d['field']} differs (CRM='{d['crm_value']}', Marketing='{d['marketing_value']}')")

    print(f"\nOutput written to {OUT_DIR}/")


if __name__ == "__main__":
    main()

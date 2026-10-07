#!/usr/bin/env python3
"""
Generate the CLM attribute map from the Solution 2 CSV column reference.

The authoritative mapping of CSV column to CLM ``Group / Attribute`` lives in
the ``smarter-docusign`` repository, at ``docs/Solution2_CSV_Column_Reference.md``,
as one markdown table per contract type. This script parses that document into
``docusign_iam/clm_attribute_map.json`` so the integration writes attributes
through exactly the same mapping the CLM workflows use.

Regenerate after editing the reference document:

    python scripts/generate_clm_attribute_map.py \
        --reference ../smarter-docusign/docs/Solution2_CSV_Column_Reference.md
"""

import argparse
import json
import os
import re
import sys

#: `| `Column_Name` | Group / Attribute<br>Group / Attribute |`
ROW = re.compile(r"^\|\s*`(?P<column>[^`]+)`\s*\|\s*(?P<targets>.+?)\s*\|\s*$")
SECTION = re.compile(r"^##\s+(?P<name>.+?)\s*$")

#: Columns that identify the row rather than carrying an attribute value.
KEY_COLUMNS = {"Document_ID"}

#: Columns used by a solution but absent from the Solution 2 reference tables.
#:
#: Solution 2 Pass 1 (`templates/Pass1_Clear_NDA_Expiration_Date.csv`) is an
#: NDA-only, clear-only run whose single mapping is
#: *CLM Agreement Details / Expiration Date*. The reference document covers the
#: V1.4 tables only, so that column is added here rather than being left
#: unmapped. See smarter-docusign/docs/Solution2_Pass1_Clear_NDA_Expiration_Date.md.
SUPPLEMENTAL = {
    "NDA": {
        "Expiration_Date": [["CLM Agreement Details", "Expiration Date"]],
    },
}


def parse_reference(path: str) -> dict:
    """Parse the reference document into {contract_type: {column: [[group, attr]]}}."""
    mapping: dict = {}
    contract_type = None

    with open(path, encoding="utf-8") as handle:
        for line in handle:
            section = SECTION.match(line)
            if section:
                contract_type = section.group("name").strip()
                mapping.setdefault(contract_type, {})
                continue

            row = ROW.match(line)
            if not row or contract_type is None:
                continue

            column = row.group("column").strip()
            if column in KEY_COLUMNS:
                continue

            targets = []
            for target in row.group("targets").split("<br>"):
                target = target.strip()
                if not target or "/" not in target:
                    continue
                group, _, attribute = target.partition("/")
                pair = [group.strip(), attribute.strip()]
                # The reference repeats a few targets; keep each one once so a
                # single value is not written twice in the same PATCH body.
                if pair not in targets:
                    targets.append(pair)

            if targets:
                mapping[contract_type][column] = targets

    for contract_type, columns in SUPPLEMENTAL.items():
        for column, targets in columns.items():
            mapping.setdefault(contract_type, {}).setdefault(column, targets)

    return {k: v for k, v in mapping.items() if v}


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        default=os.path.join(
            repo_root, "..", "smarter-docusign", "docs", "Solution2_CSV_Column_Reference.md"
        ),
        help="Path to Solution2_CSV_Column_Reference.md",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(repo_root, "docusign_iam", "clm_attribute_map.json"),
        help="Where to write the generated JSON map",
    )
    args = parser.parse_args()

    if not os.path.exists(args.reference):
        print(f"❌ Reference document not found: {args.reference}", file=sys.stderr)
        print(
            "   Clone draco9ite/smarter-docusign alongside this repo, or pass --reference.",
            file=sys.stderr,
        )
        return 1

    mapping = parse_reference(args.reference)
    payload = {
        "_source": "smarter-docusign/docs/Solution2_CSV_Column_Reference.md",
        "_generated_by": "scripts/generate_clm_attribute_map.py",
        "contract_types": mapping,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    total = sum(len(columns) for columns in mapping.values())
    print(f"✅ Wrote {args.out}")
    for contract_type, columns in sorted(mapping.items()):
        print(f"   {contract_type}: {len(columns)} columns")
    print(f"   {len(mapping)} contract types, {total} column mappings")
    return 0


if __name__ == "__main__":
    sys.exit(main())

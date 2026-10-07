#!/usr/bin/env python3
"""
Normalized models shared by the IAM and CLM sides of the integration.

The Agreement Manager API and CLM describe the same contract in different
shapes: Agreement Manager returns AI-extracted ``provisions`` and ``parties``,
while CLM stores operator-maintained values in named attribute groups. The
:class:`Agreement` dataclass is the common shape both are read into, and
:func:`build_attribute_groups` turns flat CSV-style values into the nested
payload the CLM document PATCH expects.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

#: Generated from smarter-docusign/docs/Solution2_CSV_Column_Reference.md by
#: scripts/generate_clm_attribute_map.py.
_ATTRIBUTE_MAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "clm_attribute_map.json"
)

_attribute_map_cache: Optional[Dict[str, Any]] = None


def load_attribute_map() -> Dict[str, Dict[str, List[List[str]]]]:
    """CSV column to CLM ``[group, attribute]`` targets, keyed by contract type."""
    global _attribute_map_cache
    if _attribute_map_cache is None:
        with open(_ATTRIBUTE_MAP_PATH, encoding="utf-8") as handle:
            _attribute_map_cache = json.load(handle)
    return _attribute_map_cache["contract_types"]


def supported_contract_types() -> List[str]:
    return sorted(load_attribute_map().keys())


def resolve_attribute_targets(
    contract_type: str, column: str
) -> List[Tuple[str, str]]:
    """CLM targets a CSV column writes to for one contract type.

    Falls back to treating the column as ``Group / Attribute`` only when it is
    explicitly qualified with a slash; an unmapped bare column returns no
    targets so the caller can report it rather than silently writing nowhere.
    """
    mapping = load_attribute_map().get(contract_type, {})
    if column in mapping:
        return [(group, attribute) for group, attribute in mapping[column]]
    if "/" in column:
        group, _, attribute = column.partition("/")
        return [(group.strip(), attribute.strip())]
    return []


def build_attribute_groups(
    contract_type: str, values: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """Build a CLM ``AttributeGroups`` PATCH body from flat column values.

    Args:
        contract_type: One of :func:`supported_contract_types`, e.g. ``"NDA"``.
        values: Column name to value, as read from a metadata CSV row. A value
            of ``""`` clears the attribute; ``None`` omits it entirely, leaving
            the stored value untouched.

    Returns:
        ``(attribute_groups, unmapped_columns)``. ``attribute_groups`` is ready
        to pass to :meth:`CLMClient.update_document_attributes`.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    unmapped: List[str] = []

    for column, value in values.items():
        if value is None:
            continue
        targets = resolve_attribute_targets(contract_type, column)
        if not targets:
            unmapped.append(column)
            continue
        for group_name, attribute_name in targets:
            groups.setdefault(group_name, {})[attribute_name] = {"Value": value}

    return groups, unmapped


@dataclass
class Party:
    """A counterparty on an agreement."""

    name: str = ""
    role: str = ""
    party_id: str = ""

    @classmethod
    def from_agreement_manager(cls, payload: Dict[str, Any]) -> "Party":
        return cls(
            name=payload.get("name_in_agreement") or payload.get("name") or "",
            role=payload.get("role") or "",
            party_id=str(payload.get("id") or ""),
        )


@dataclass
class Agreement:
    """One agreement, however it was sourced.

    ``source`` records which system the record came from (``agreement_manager``
    or ``clm``) so a reconciliation report can say where each value originated.
    """

    id: str = ""
    name: str = ""
    contract_type: str = ""
    status: str = ""
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    total_value: Optional[float] = None
    currency: Optional[str] = None
    parties: List[Party] = field(default_factory=list)
    source: str = ""
    clm_document_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_agreement_manager(cls, payload: Dict[str, Any]) -> "Agreement":
        """Read an Agreement Manager API agreement record."""
        provisions = payload.get("provisions") or {}
        value = provisions.get("total_agreement_value")
        return cls(
            id=str(payload.get("id") or ""),
            name=payload.get("file_name") or payload.get("title") or "",
            contract_type=_type_name(payload.get("type")),
            status=payload.get("status") or "",
            effective_date=provisions.get("effective_date"),
            expiration_date=provisions.get("expiration_date"),
            total_value=_as_float(value),
            currency=provisions.get("total_agreement_value_currency_code"),
            parties=[
                Party.from_agreement_manager(p) for p in payload.get("parties") or []
            ],
            source="agreement_manager",
            # Agreement Manager records a source_id when the agreement was
            # ingested from CLM, which is the link back to the CLM document.
            clm_document_id=payload.get("source_id") or None,
            raw=payload,
        )

    @classmethod
    def from_clm_document(cls, payload: Dict[str, Any]) -> "Agreement":
        """Read a CLM document, pulling dates out of its attribute groups."""
        groups = payload.get("AttributeGroups") or {}
        admin = groups.get("Contract Status & Administrative Metadata") or {}
        clm_details = groups.get("CLM Agreement Details") or {}

        return cls(
            id=str(payload.get("Id") or payload.get("Uid") or ""),
            name=payload.get("Name") or "",
            contract_type=_attribute_value(admin.get("Document Type")) or "",
            status=_attribute_value(admin.get("Status")) or "",
            effective_date=(
                _attribute_value(admin.get("Effective Date"))
                or _attribute_value(clm_details.get("Effective Date"))
            ),
            expiration_date=(
                _attribute_value(admin.get("Expiration Date"))
                or _attribute_value(clm_details.get("Expiration Date"))
            ),
            source="clm",
            clm_document_id=str(payload.get("Id") or payload.get("Uid") or ""),
            raw=payload,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializable form, without the bulky raw payload."""
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass
class FieldDifference:
    """One field that disagrees between the two systems."""

    field_name: str
    agreement_manager_value: Any
    clm_value: Any

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


#: Fields compared when reconciling an Agreement Manager record against CLM.
RECONCILED_FIELDS = ("contract_type", "status", "effective_date", "expiration_date")


def diff_agreements(
    agreement_manager: Agreement, clm: Agreement
) -> List[FieldDifference]:
    """Fields where the IAM repository and CLM disagree.

    A value missing on one side is reported as a difference, because for the
    migration work an empty CLM attribute is exactly the case worth seeing.
    """
    differences = []
    for name in RECONCILED_FIELDS:
        left = getattr(agreement_manager, name)
        right = getattr(clm, name)
        if _normalize(left) != _normalize(right):
            differences.append(FieldDifference(name, left, right))
    return differences


# ── Helpers ─────────────────────────────────────────────────────────────


def _attribute_value(attribute: Any) -> Optional[str]:
    """Pull the scalar out of a CLM attribute, which may be wrapped or bare."""
    if attribute is None:
        return None
    if isinstance(attribute, dict):
        value = attribute.get("Value", attribute.get("value"))
        return None if value is None else str(value)
    return str(attribute)


def _type_name(agreement_type: Any) -> str:
    """Agreement Manager returns the type as a string or an object."""
    if isinstance(agreement_type, dict):
        return agreement_type.get("name") or agreement_type.get("value") or ""
    return agreement_type or ""


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("amount", value.get("value"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value: Any) -> str:
    """Compare loosely: dates may carry a time component, case may differ."""
    if value is None:
        return ""
    text = str(value).strip()
    if "T" in text and len(text) >= 10 and text[4] == "-":
        text = text.split("T", 1)[0]
    return text.casefold()

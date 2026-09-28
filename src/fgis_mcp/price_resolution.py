"""Deterministic, evidence-preserving resolution of quarterly resource prices."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MONEY = Decimal("0.01")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def _signature(record: dict) -> tuple:
    """Fields that must agree when a split form repeats the same resource."""
    return (
        record.get("code"),
        record.get("name"),
        record.get("unit"),
        _decimal(record.get("price_current")),
        _decimal(record.get("price_base")),
        _decimal(record.get("price_release")),
        _decimal(record.get("index")),
        record.get("group_no"),
        record.get("group_name"),
    )


def resolve_resource_price(
    records: list[dict], *, code: str, zone_id: int, period_id: int, dataset_id: str
) -> dict:
    """Resolve one exact quarterly record without silently filling missing context.

    A published current price wins. When it is absent, a published base price and
    group index may be multiplied. Missing or conflicting evidence stays unresolved.
    """
    exact = [
        row
        for row in records
        if row.get("code") == code and row.get("zone_id") == zone_id and row.get("period_id") == period_id
    ]
    base = {
        "dataset_id": dataset_id,
        "code": code,
        "zone_id": zone_id,
        "period_id": period_id,
        "evidence_count": len(exact),
    }
    if not exact:
        return base | {
            "status": "PRICE_NOT_FOUND",
            "error_code": "PRICE_NOT_FOUND",
            "resolved_price": None,
            "resolution_method": "unresolved",
            "requires_index": None,
            "message": "No exact resource price record for the requested code, zone and period.",
        }

    signatures = {_signature(row) for row in exact}
    if len(signatures) != 1:
        return base | {
            "status": "AMBIGUOUS_PRICE_RECORD",
            "error_code": "AMBIGUOUS_PRICE_RECORD",
            "resolved_price": None,
            "resolution_method": "unresolved",
            "requires_index": None,
            "message": "Conflicting exact records exist; no price was selected.",
            "records": exact,
        }

    record = exact[0]
    current = _decimal(record.get("price_current"))
    price_base = _decimal(record.get("price_base"))
    index = _decimal(record.get("index"))
    if current is not None:
        resolved = current
        method = "published_current_price"
        requires_index = False
        formula = "price_current"
        price_kind = "current_estimated"
    elif price_base is not None and index is not None:
        resolved = price_base * index
        method = "indexed_base_price"
        requires_index = True
        formula = "price_base * index"
        price_kind = "current_estimated_from_base_and_index"
    else:
        missing = []
        if price_base is None:
            missing.append("price_base")
        if index is None:
            missing.append("index")
        return base | {
            "status": "PRICE_COMPONENTS_MISSING",
            "error_code": "PRICE_COMPONENTS_MISSING",
            "name": record.get("name"),
            "unit": record.get("unit"),
            "price_base": record.get("price_base"),
            "price_current": record.get("price_current"),
            "index": record.get("index"),
            "group_no": record.get("group_no"),
            "group_name": record.get("group_name"),
            "resolved_price": None,
            "resolution_method": "unresolved",
            "requires_index": None,
            "missing_fields": missing,
            "message": "The exact record does not contain enough published fields to resolve a price.",
            "provenance": record.get("provenance"),
        }

    return base | {
        "status": "complete",
        "error_code": None,
        "name": record.get("name"),
        "unit": record.get("unit"),
        "price_kind": price_kind,
        "price_release": record.get("price_release"),
        "price_base": record.get("price_base"),
        "price_current": record.get("price_current"),
        "index": record.get("index"),
        "group_no": record.get("group_no"),
        "group_name": record.get("group_name"),
        "resolved_price": _money(resolved),
        "resolution_method": method,
        "requires_index": requires_index,
        "formula": formula,
        "source_rows": [
            {"price_id": row.get("price_id"), "sheet": row.get("sheet"), "row": row.get("row")}
            for row in exact
        ],
        "provenance": record.get("provenance"),
        "message": "Price resolved from an exact code, zone and period match.",
    }

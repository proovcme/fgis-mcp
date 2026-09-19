"""Comparison of norm cards and price records across different editions and periods."""

from typing import Any


def compare_norms(norm_a: dict[str, Any], norm_b: dict[str, Any]) -> dict[str, Any]:
    """Compare two norm records (typically from different editions or documents).

    Tracks differences in:
    - Name and unit of measurement
    - Work steps (added, removed, changed)
    - Resource quantities, units, and presence
    - Source edition metadata
    """
    code = norm_a.get("code") or norm_b.get("code")
    doc_a = (norm_a.get("source") or {}).get("document") or norm_a.get("edition") or "Редакция A"
    doc_b = (norm_b.get("source") or {}).get("document") or norm_b.get("edition") or "Редакция B"

    # Name and unit differences
    name_changed = norm_a.get("name") != norm_b.get("name")
    unit_changed = norm_a.get("unit") != norm_b.get("unit")

    # Work steps diff
    steps_a = norm_a.get("work_steps") or []
    steps_b = norm_b.get("work_steps") or []
    steps_added = [s for s in steps_b if s not in steps_a]
    steps_removed = [s for s in steps_a if s not in steps_b]

    # Resources diff
    res_a = {r.get("code"): r for r in (norm_a.get("resources") or []) if r.get("code")}
    res_b = {r.get("code"): r for r in (norm_b.get("resources") or []) if r.get("code")}

    codes_a = set(res_a.keys())
    codes_b = set(res_b.keys())

    added_resources = [res_b[c] for c in sorted(codes_b - codes_a)]
    removed_resources = [res_a[c] for c in sorted(codes_a - codes_b)]

    modified_resources = []
    unchanged_resources = []

    for c in sorted(codes_a & codes_b):
        item_a = res_a[c]
        item_b = res_b[c]
        qty_a = item_a.get("quantity")
        qty_b = item_b.get("quantity")
        raw_a = item_a.get("quantity_raw")
        raw_b = item_b.get("quantity_raw")
        unit_a = item_a.get("unit")
        unit_b = item_b.get("unit")

        if qty_a != qty_b or raw_a != raw_b or unit_a != unit_b:
            modified_resources.append(
                {
                    "code": c,
                    "name": item_b.get("name") or item_a.get("name"),
                    "before": {
                        "quantity": qty_a,
                        "quantity_raw": raw_a,
                        "unit": unit_a,
                    },
                    "after": {
                        "quantity": qty_b,
                        "quantity_raw": raw_b,
                        "unit": unit_b,
                    },
                }
            )
        else:
            unchanged_resources.append(c)

    has_differences = bool(
        name_changed
        or unit_changed
        or steps_added
        or steps_removed
        or added_resources
        or removed_resources
        or modified_resources
    )

    summary_items = []
    if name_changed:
        summary_items.append(f"Изменено наименование: «{norm_a.get('name')}» → «{norm_b.get('name')}»")
    if unit_changed:
        summary_items.append(f"Изменена единица измерения: {norm_a.get('unit')} → {norm_b.get('unit')}")
    if steps_added:
        summary_items.append(f"Добавлено этапов работ: {len(steps_added)}")
    if steps_removed:
        summary_items.append(f"Удалено этапов работ: {len(steps_removed)}")
    if added_resources:
        summary_items.append(f"Добавлено ресурсов: {len(added_resources)}")
    if removed_resources:
        summary_items.append(f"Удалено ресурсов: {len(removed_resources)}")
    if modified_resources:
        summary_items.append(f"Изменено норм расхода ресурсов: {len(modified_resources)}")
    if not has_differences:
        summary_items.append("Различий в составе работ, единице измерения и ресурсах не обнаружено.")

    return {
        "code": code,
        "edition_a": {
            "document": doc_a,
            "guid": (norm_a.get("source") or {}).get("document_guid"),
            "name": norm_a.get("name"),
            "unit": norm_a.get("unit"),
        },
        "edition_b": {
            "document": doc_b,
            "guid": (norm_b.get("source") or {}).get("document_guid"),
            "name": norm_b.get("name"),
            "unit": norm_b.get("unit"),
        },
        "has_differences": has_differences,
        "summary": summary_items,
        "details": {
            "name_changed": name_changed,
            "unit_changed": unit_changed,
            "work_steps": {
                "added": steps_added,
                "removed": steps_removed,
                "unchanged_count": len(set(steps_a) & set(steps_b)),
            },
            "resources": {
                "added": added_resources,
                "removed": removed_resources,
                "modified": modified_resources,
                "unchanged_count": len(unchanged_resources),
            },
        },
    }

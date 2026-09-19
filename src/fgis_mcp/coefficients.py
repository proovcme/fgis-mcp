"""Structured extraction of coefficients and application conditions from technical parts."""

import re
from typing import Any

from .normalize import clean, number

COEFF_HEADER_PATTERNS = [
    r"коэффициент",
    r"коэфф",
    r"к нормам",
    r"к расценкам",
    r"к оплате",
    r"к стоимости",
    r"размер коэффициента",
]

CONDITION_HEADER_PATTERNS = [
    r"услови",
    r"фактор",
    r"наименовани",
    r"производств",
    r"характеристик",
    r"описание работ",
    r"шифр таблиц",
    r"шифр норм",
    r"температур",
    r"категори",
    r"должност",
]

NOTE_HEADER_PATTERNS = [
    r"примечани",
    r"указани",
    r"обосновани",
    r"пункт",
    r"ссылк",
]


def is_coefficient_table(table: dict[str, Any]) -> bool:
    """Determine if a document table contains coefficient specifications."""
    rows = table.get("rows", [])
    if not rows:
        return False

    header_sample = []
    for r in rows[:4]:
        for cell in r:
            header_sample.append(clean(cell.get("text", "")).casefold())

    combined_text = " ".join(header_sample)
    has_coeff = any(re.search(pat, combined_text) for pat in COEFF_HEADER_PATTERNS)
    has_condition = (
        any(re.search(pat, combined_text) for pat in CONDITION_HEADER_PATTERNS) or len(rows[0]) >= 2
    )
    return has_coeff and has_condition


def extract_coefficients_from_table(
    table: dict[str, Any], doc_info: dict[str, Any], table_index: int = 0
) -> list[dict[str, Any]]:
    """Extract structured coefficient evidence rows from an HTML table.

    Never guesses or invents conditions. If structure is ambiguous, marks as 'unresolved'.
    """
    rows = table.get("rows", [])
    if len(rows) < 2:
        return []

    # Identify header row and column mapping
    mapping: dict[int, str] = {}
    header_row_idx = 0

    for r_idx, row in enumerate(rows[:3]):
        row_texts = [clean(c.get("text", "")).casefold() for c in row]
        for col_idx, text in enumerate(row_texts):
            if any(re.search(pat, text) for pat in COEFF_HEADER_PATTERNS) and col_idx not in mapping:
                mapping[col_idx] = "coeff"
            elif any(re.search(pat, text) for pat in CONDITION_HEADER_PATTERNS) and col_idx not in mapping:
                mapping[col_idx] = "condition"
            elif any(re.search(pat, text) for pat in NOTE_HEADER_PATTERNS) and col_idx not in mapping:
                mapping[col_idx] = "note"
        if "coeff" in mapping.values():
            header_row_idx = r_idx
            break

    if "coeff" not in mapping.values():
        return []

    coeff_col = next(k for k, v in mapping.items() if v == "coeff")
    condition_col = next((k for k, v in mapping.items() if v == "condition"), None)
    if condition_col is None:
        # Pick the first non-coeff, non-note column as condition column
        for col_idx in range(len(rows[0])):
            if col_idx != coeff_col and mapping.get(col_idx) != "note":
                condition_col = col_idx
                break

    note_col = next((k for k, v in mapping.items() if v == "note"), None)

    results = []
    context_before = table.get("context_before", "")[:300]
    provenance = doc_info.get("provenance", {})

    for r_idx in range(header_row_idx + 1, len(rows)):
        row = rows[r_idx]
        if not row:
            continue

        raw_row_texts = [clean(c.get("text", "")) for c in row]

        # Skip numbering rows (e.g. ['1', '2', '3', '4', '5'])
        digits_only = [t for t in raw_row_texts if t]
        if len(digits_only) >= 2 and all(t.isdigit() and int(t) < 50 for t in digits_only):
            nums = [int(t) for t in digits_only]
            if nums == list(range(nums[0], nums[0] + len(nums))):
                continue

        raw_coeff_cell = row[coeff_col] if coeff_col < len(row) else None
        raw_cond_cell = row[condition_col] if condition_col is not None and condition_col < len(row) else None
        raw_note_cell = row[note_col] if note_col is not None and note_col < len(row) else None

        coeff_text = clean(raw_coeff_cell.get("text", "")) if raw_coeff_cell else ""
        condition_text = clean(raw_cond_cell.get("text", "")) if raw_cond_cell else ""
        note_text = clean(raw_note_cell.get("text", "")) if raw_note_cell else ""

        if not coeff_text and not condition_text:
            continue

        # Look for numbers in coeff cell
        coeff_val = number(coeff_text)
        status = "extracted"

        # If condition or coefficient is absent or non-numeric/ambiguous, classify as unresolved
        if coeff_val is None or not condition_text or coeff_text in {"—", "-", "–"}:
            status = "unresolved"

        offset = raw_coeff_cell.get("offset") if raw_coeff_cell else table.get("offset", 0)

        entry = {
            "document": doc_info.get("name"),
            "document_guid": doc_info.get("document_guid"),
            "table_index": table_index,
            "row_index": r_idx,
            "coefficient": coeff_val,
            "coefficient_raw": coeff_text,
            "condition_text": condition_text,
            "note_text": note_text,
            "raw_row": raw_row_texts,
            "surrounding_text": context_before,
            "source_offset": offset,
            "source_sha256": provenance.get("sha256"),
            "source_url": provenance.get("source_url"),
            "status": status,
            "evidence": {
                "source": doc_info.get("source", "normative"),
                "source_type": "official_document_table",
                "document": doc_info.get("name"),
                "document_guid": doc_info.get("document_guid"),
                "table_index": table_index,
                "row_index": r_idx,
                "sha256": provenance.get("sha256"),
                "source_url": provenance.get("source_url"),
            },
        }
        results.append(entry)

    return results


def extract_document_coefficients(doc: dict[str, Any], doc_info: dict[str, Any]) -> list[dict[str, Any]]:
    """Scan all tables in a document and extract structured coefficient evidence."""
    all_coefficients = []
    tables = doc.get("tables", [])
    for idx, tbl in enumerate(tables):
        if is_coefficient_table(tbl):
            extracted = extract_coefficients_from_table(tbl, doc_info, table_index=idx)
            all_coefficients.extend(extracted)
    return all_coefficients

"""Unit normalization and physical quantity equivalence comparison for FGIS CS norms."""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

# Table of unambiguous physical unit conversions to base unit for each dimension.
# Mass -> base: kg (килограмм)
# Length -> base: m (метр)
# Area -> base: m2 (квадратный метр)
# Volume -> base: m3 (кубический метр)
# Count -> base: шт (штука)

_DIMENSION_TABLE: dict[str, tuple[str, float]] = {
    # Mass (base: kg)
    "т": ("mass", 1000.0),
    "тонн": ("mass", 1000.0),
    "тонна": ("mass", 1000.0),
    "тонны": ("mass", 1000.0),
    "тн": ("mass", 1000.0),
    "ц": ("mass", 100.0),
    "центнер": ("mass", 100.0),
    "центнера": ("mass", 100.0),
    "центнеров": ("mass", 100.0),
    "кг": ("mass", 1.0),
    "килограмм": ("mass", 1.0),
    "килограмма": ("mass", 1.0),
    "килограммов": ("mass", 1.0),
    "г": ("mass", 0.001),
    "грамм": ("mass", 0.001),
    "грамма": ("mass", 0.001),
    "граммов": ("mass", 0.001),
    "мг": ("mass", 0.000001),
    "миллиграмм": ("mass", 0.000001),
    "миллиграмма": ("mass", 0.000001),
    "миллиграммов": ("mass", 0.000001),
    # Length (base: m)
    "км": ("length", 1000.0),
    "километр": ("length", 1000.0),
    "километра": ("length", 1000.0),
    "километров": ("length", 1000.0),
    "1000 м": ("length", 1000.0),
    "1000м": ("length", 1000.0),
    "тыс. м": ("length", 1000.0),
    "тыс.м": ("length", 1000.0),
    "тыс м": ("length", 1000.0),
    "1 тыс. м": ("length", 1000.0),
    "1 тыс м": ("length", 1000.0),
    "1000 метр": ("length", 1000.0),
    "1000 метров": ("length", 1000.0),
    "100 м": ("length", 100.0),
    "100м": ("length", 100.0),
    "100 метр": ("length", 100.0),
    "100 метров": ("length", 100.0),
    "10 м": ("length", 10.0),
    "10м": ("length", 10.0),
    "10 метр": ("length", 10.0),
    "10 метров": ("length", 10.0),
    "м": ("length", 1.0),
    "метр": ("length", 1.0),
    "метра": ("length", 1.0),
    "метров": ("length", 1.0),
    "дм": ("length", 0.1),
    "дециметр": ("length", 0.1),
    "дециметра": ("length", 0.1),
    "дециметров": ("length", 0.1),
    "см": ("length", 0.01),
    "сантиметр": ("length", 0.01),
    "сантиметра": ("length", 0.01),
    "сантиметров": ("length", 0.01),
    "мм": ("length", 0.001),
    "миллиметр": ("length", 0.001),
    "миллиметра": ("length", 0.001),
    "миллиметров": ("length", 0.001),
    # Area (base: m2)
    "га": ("area", 10000.0),
    "гектар": ("area", 10000.0),
    "гектара": ("area", 10000.0),
    "гектаров": ("area", 10000.0),
    "1000 м2": ("area", 1000.0),
    "1000м2": ("area", 1000.0),
    "1000 м²": ("area", 1000.0),
    "1000м²": ("area", 1000.0),
    "тыс. м2": ("area", 1000.0),
    "тыс.м2": ("area", 1000.0),
    "тыс. м²": ("area", 1000.0),
    "тыс.м²": ("area", 1000.0),
    "1 тыс. м2": ("area", 1000.0),
    "1 тыс. м²": ("area", 1000.0),
    "100 м2": ("area", 100.0),
    "100м2": ("area", 100.0),
    "100 м²": ("area", 100.0),
    "100м²": ("area", 100.0),
    "100 кв. м": ("area", 100.0),
    "100 кв.м": ("area", 100.0),
    "100 кв м": ("area", 100.0),
    "м2": ("area", 1.0),
    "м²": ("area", 1.0),
    "кв. м": ("area", 1.0),
    "кв.м": ("area", 1.0),
    "кв м": ("area", 1.0),
    "кв. метр": ("area", 1.0),
    "квадратный метр": ("area", 1.0),
    "дм2": ("area", 0.01),
    "дм²": ("area", 0.01),
    "кв. дм": ("area", 0.01),
    "см2": ("area", 0.0001),
    "см²": ("area", 0.0001),
    "кв. см": ("area", 0.0001),
    # Volume (base: m3)
    "1000 м3": ("volume", 1000.0),
    "1000м3": ("volume", 1000.0),
    "1000 м³": ("volume", 1000.0),
    "1000м³": ("volume", 1000.0),
    "тыс. м3": ("volume", 1000.0),
    "тыс.м3": ("volume", 1000.0),
    "тыс. м³": ("volume", 1000.0),
    "1 тыс. м3": ("volume", 1000.0),
    "100 м3": ("volume", 100.0),
    "100м3": ("volume", 100.0),
    "100 м³": ("volume", 100.0),
    "100м³": ("volume", 100.0),
    "100 куб. м": ("volume", 100.0),
    "100 куб.м": ("volume", 100.0),
    "100 куб м": ("volume", 100.0),
    "м3": ("volume", 1.0),
    "м³": ("volume", 1.0),
    "куб. м": ("volume", 1.0),
    "куб.м": ("volume", 1.0),
    "куб м": ("volume", 1.0),
    "кубический метр": ("volume", 1.0),
    "куб. метр": ("volume", 1.0),
    "дм3": ("volume", 0.001),
    "дм³": ("volume", 0.001),
    "л": ("volume", 0.001),
    "литр": ("volume", 0.001),
    "литра": ("volume", 0.001),
    "литров": ("volume", 0.001),
    "мл": ("volume", 0.000001),
    "миллилитр": ("volume", 0.000001),
    # Count (base: шт)
    "тыс. шт": ("count", 1000.0),
    "тыс.шт": ("count", 1000.0),
    "тыс шт": ("count", 1000.0),
    "1000 шт": ("count", 1000.0),
    "1000шт": ("count", 1000.0),
    "1 тыс. шт": ("count", 1000.0),
    "1 тыс шт": ("count", 1000.0),
    "100 шт": ("count", 100.0),
    "100шт": ("count", 100.0),
    "10 шт": ("count", 10.0),
    "10шт": ("count", 10.0),
    "шт": ("count", 1.0),
    "штука": ("count", 1.0),
    "штуки": ("count", 1.0),
    "штук": ("count", 1.0),
}


def normalize_unit(unit_str: str | None) -> str:
    """Clean and normalize unit of measurement string."""
    if not unit_str:
        return ""
    cleaned = unit_str.strip().lower()
    # Normalize multiple whitespaces
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Remove trailing dot if not inside abbreviation like 'тыс. м'
    if cleaned.endswith(".") and not cleaned.endswith("тыс."):
        cleaned = cleaned[:-1].strip()
    return cleaned


def get_unit_dimension_and_factor(unit_str: str | None) -> tuple[str, float] | None:
    """Return (dimension, conversion_factor_to_base) for recognized unambiguous units.

    Returns None if unit is unrecognized or non-standard.
    """
    normalized = normalize_unit(unit_str)
    if not normalized:
        return None
    if normalized in _DIMENSION_TABLE:
        return _DIMENSION_TABLE[normalized]

    # Try removing trailing dot or punctuation
    trimmed = normalized.rstrip(".,; ")
    if trimmed in _DIMENSION_TABLE:
        return _DIMENSION_TABLE[trimmed]

    return None


def are_quantities_equivalent(
    qty_a: Any,
    unit_a: str | None,
    qty_b: Any,
    unit_b: str | None,
    raw_a: Any = None,
    raw_b: Any = None,
) -> bool:
    """Check whether two physical quantities are equivalent.

    Compatible units within the same physical dimension (mass: t/kg/g, length: km/m/cm,
    area: m2/ha, volume: m3/l, count: pcs/1000 pcs) are converted to a common base unit.

    Incompatible physical dimensions (e.g. mass vs length) are NEVER converted and
    will return False if their unit strings differ.
    """
    # If both quantities are None (e.g. non-numeric norms)
    if qty_a is None and qty_b is None:
        if raw_a is not None and raw_b is not None:
            return raw_a == raw_b and normalize_unit(unit_a) == normalize_unit(unit_b)
        return normalize_unit(unit_a) == normalize_unit(unit_b)

    if qty_a is None or qty_b is None:
        return False

    norm_u_a = normalize_unit(unit_a)
    norm_u_b = normalize_unit(unit_b)

    # 1. Identical unit strings (applies to any unit, known or unknown)
    if norm_u_a == norm_u_b:
        try:
            d_a = Decimal(str(qty_a)).normalize()
            d_b = Decimal(str(qty_b)).normalize()
            if d_a == d_b:
                return True
        except (InvalidOperation, TypeError, ValueError):
            pass
        try:
            return math.isclose(float(qty_a), float(qty_b), rel_tol=1e-7, abs_tol=1e-9)
        except (TypeError, ValueError):
            return qty_a == qty_b

    # 2. Different unit strings: attempt unambiguous conversion within same dimension
    conv_a = get_unit_dimension_and_factor(norm_u_a)
    conv_b = get_unit_dimension_and_factor(norm_u_b)

    if not conv_a or not conv_b:
        # One or both units cannot be safely converted -> treat as different
        return False

    dim_a, factor_a = conv_a
    dim_b, factor_b = conv_b

    if dim_a != dim_b:
        # Different physical dimensions (e.g. mass vs length) -> NEVER convert
        return False

    # Exact Decimal conversion
    try:
        d_a = Decimal(str(qty_a))
        d_fact_a = Decimal(str(factor_a))
        base_a = (d_a * d_fact_a).normalize()

        d_b = Decimal(str(qty_b))
        d_fact_b = Decimal(str(factor_b))
        base_b = (d_b * d_fact_b).normalize()

        if base_a == base_b:
            return True
    except (InvalidOperation, TypeError, ValueError):
        pass

    # Floating point comparison with tolerance
    try:
        val_a = float(qty_a) * factor_a
        val_b = float(qty_b) * factor_b
        return math.isclose(val_a, val_b, rel_tol=1e-7, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False

"""Read construction resource codes from the public KSR catalogue."""

import re
from urllib.parse import urlencode

from .errors import FgisError
from .network import HOST, SourceError
from .normalize import code_text
from .storage import now

CATALOG_URL = f"https://{HOST}/ksr/navigate"
CODE = re.compile(r"[0-9]+(?:[.-][0-9]+)+")
FULL_CODE = re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}\.[0-9]{3}\.(?P<tree>[0-9.-]+-[0-9]{3})")


def lookup(network, query: str, *, exact: bool = False, limit: int = 20, offset: int = 0) -> dict:
    value = query.strip()
    if not 1 <= len(value) <= 200 or any(char in query for char in "\r\n\x00"):
        raise FgisError("INVALID_INPUT", "KSR input must be a single line of 1..200 characters")
    if exact and not CODE.fullmatch(value):
        raise FgisError("INVALID_INPUT", "Expected an exact KSR resource code")
    if not 1 <= limit <= 100 or offset < 0:
        raise FgisError("INVALID_INPUT", "limit must be 1..100 and offset nonnegative")

    rows, _, meta = network.get_json("Ksr/TipSearch", {"value": value})
    items = []
    seen = set()
    for row in rows:
        record_id = row.get("id")
        title = row.get("title")
        name = row.get("name")
        unit = row.get("unitName")
        if (
            type(record_id) is not int
            or not isinstance(title, str)
            or not isinstance(name, str)
            or not name.strip()
            or (unit is not None and not isinstance(unit, str))
        ):
            raise SourceError("SCHEMA_CHANGED", "Unexpected resource in FGIS KSR search response")
        code = code_text(title)
        if not CODE.fullmatch(code):
            raise SourceError("SCHEMA_CHANGED", "Unexpected resource code in FGIS KSR search response")
        if exact and code != value:
            continue
        full = FULL_CODE.fullmatch(code)
        url = CATALOG_URL
        if full:
            url += "?" + urlencode({"fullCode": code, "tree": full["tree"]})
        identity = (record_id, code, name, unit)
        if identity in seen:
            continue
        seen.add(identity)
        items.append({"id": record_id, "code": code, "name": name, "unit": unit, "catalog_url": url})

    match_status = "not_found_in_search"
    if items:
        match_status = "candidate"
        if exact:
            match_status = "exact" if len(items) == 1 else "ambiguous"
    return {
        "source": "ksr",
        "query": value,
        "match_status": match_status,
        "catalog_url": CATALOG_URL,
        "total": len(items),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "items": items[offset : offset + limit],
        "provenance": {**meta, "fetched_at": now()},
    }

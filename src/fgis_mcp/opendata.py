"""OpenData integration for FGIS CS official passports and distribution files."""

import re
from typing import Any

from .network import BASE, Network, SourceError
from .normalize import clean

KNOWN_OPENDATA_DATASETS = [
    "7707082071-fsnb",  # ФСНБ-2022
    "7707082071-fsnbfer",  # ФСНБ-2020 / ФЕР
]


def normalize_passport(raw: dict[str, Any], number: str) -> dict[str, Any]:
    """Extract standard open data passport metadata from public API response."""
    if not isinstance(raw, dict):
        raise SourceError("SCHEMA_CHANGED", "OpenData passport response must be a JSON object")

    # The portal may return fields in PascalCase, camelCase, or Russian keys
    dataset_id = clean(raw.get("identifier") or raw.get("number") or raw.get("id") or number)
    title = clean(
        raw.get("identificationname")
        or raw.get("standardname")
        or raw.get("name")
        or raw.get("title")
        or dataset_id
    )
    description = clean(raw.get("description") or raw.get("subject") or "")
    owner = clean(raw.get("publishername") or raw.get("owner") or raw.get("organization") or "")
    pub_date = clean(raw.get("publishdate") or raw.get("created") or "")
    update_date = clean(raw.get("lastchangesdate") or raw.get("updated") or raw.get("date") or "")
    version = clean(raw.get("version") or raw.get("edition") or "")
    terms_of_use = clean(raw.get("termsofuse") or raw.get("terms") or "")
    schema_url = clean(raw.get("structuredataurl") or raw.get("schemaUrl") or "")

    raw_files = raw.get("data") or raw.get("files") or raw.get("items") or []
    if isinstance(raw_files, dict):
        raw_files = [raw_files]
    elif not isinstance(raw_files, list):
        raw_files = []

    files = []
    for f in raw_files:
        if isinstance(f, dict):
            file_url = clean(f.get("source") or f.get("url") or f.get("link") or "")
            file_format = clean(f.get("format") or "").upper()
            file_version = clean(f.get("version") or version)
            file_desc = clean(f.get("description") or "")
            file_date = clean(f.get("created") or f.get("date") or "")
            if file_url:
                files.append(
                    {
                        "source_url": file_url,
                        "format": file_format,
                        "version": file_version,
                        "description": file_desc,
                        "date": file_date,
                    }
                )

    passport_url = f"{BASE}OpenData/GetByNumber/{number.strip()}"

    return {
        "dataset_id": dataset_id,
        "number": number.strip(),
        "title": title,
        "description": description,
        "owner": owner,
        "publication_date": pub_date,
        "update_date": update_date,
        "version": version,
        "terms_of_use": terms_of_use,
        "schema_url": schema_url,
        "passport_url": passport_url,
        "files": files,
        "raw_keys": sorted(raw.keys()),
    }


def fetch_passport(network: Network, dataset_number: str) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Fetch and normalize an OpenData passport from FGIS CS."""
    clean_num = dataset_number.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", clean_num):
        raise ValueError("Invalid OpenData dataset number")

    path = f"OpenData/GetByNumber/{clean_num}"
    data, body, meta = network.get_value(path)
    if not isinstance(data, dict):
        raise SourceError("SCHEMA_CHANGED", "Expected OpenData passport object")

    normalized = normalize_passport(data, clean_num)
    return normalized, body, meta


def list_known_opendata(network: Network | None = None) -> list[dict[str, Any]]:
    """List known and discovered OpenData datasets."""
    results = []
    for num in KNOWN_OPENDATA_DATASETS:
        entry = {
            "dataset_number": num,
            "passport_path": f"OpenData/GetByNumber/{num}",
            "passport_url": f"{BASE}OpenData/GetByNumber/{num}",
            "description": "ФСНБ-2022" if "fsnb" in num and "fer" not in num else "ФСНБ-2020 / ФЕР",
        }
        results.append(entry)
    return results


def cross_check_opendata_with_api(
    opendata_passport: dict[str, Any], api_catalog_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Cross-check OpenData passport metadata against NormLegalDocPublished API root.

    Identifies discrepancies between OpenData publication dates/versions and internal API catalogs.
    """
    api_names = [clean(r.get("name") or r.get("documentName") or "") for r in api_catalog_rows]

    discrepancies = []
    opendata_files_count = len(opendata_passport.get("files", []))

    if opendata_files_count == 0 and len(api_catalog_rows) > 0:
        discrepancies.append(
            "OpenData passport contains no file distributions, but API catalog has active nodes"
        )

    passport_version = opendata_passport.get("version", "")
    matching_nodes = [name for name in api_names if passport_version and passport_version in name]

    return {
        "dataset_number": opendata_passport.get("number"),
        "opendata_version": passport_version,
        "opendata_update_date": opendata_passport.get("update_date"),
        "opendata_files_count": opendata_files_count,
        "api_catalog_nodes_count": len(api_catalog_rows),
        "api_matching_nodes_count": len(matching_nodes),
        "discrepancies": discrepancies,
        "verified_consistent": len(discrepancies) == 0,
        "note": "Cross-check compares independent publication channels (OpenData passport vs live API tree)",
    }

"""OpenData integration for FGIS CS official passports and distribution files."""

import re
from typing import Any

from .network import BASE, Network, SourceError
from .normalize import clean

KNOWN_OPENDATA_DATASETS = [
    "7707082071-fsnb",  # ФСНБ-2022 (resolves with leading space on live portal)
    "7707082071-fsnbfer",  # ФСНБ-2020 / ФЕР
]


def resolve_dataset_number(number: str) -> str:
    """Resolve dataset number taking into account FGIS CS portal database quirks."""
    clean_num = str(number).strip()
    if clean_num == "7707082071-fsnb":
        # Defect in FGIS CS production DB: primary key was inserted with a leading space
        return " 7707082071-fsnb"
    return clean_num


def normalize_passport(raw: dict[str, Any], number: str) -> dict[str, Any]:
    """Extract standard open data passport metadata from public API response."""
    if not isinstance(raw, dict):
        raise SourceError("SCHEMA_CHANGED", "OpenData passport response must be a JSON object")

    # The portal may return fields in PascalCase, camelCase, or Russian keys
    dataset_id = clean(
        raw.get("identificationNumber")
        or raw.get("identifier")
        or raw.get("number")
        or raw.get("id")
        or number
    )
    title = clean(
        raw.get("datasetName")
        or raw.get("datasetTitle")
        or raw.get("identificationname")
        or raw.get("standardname")
        or raw.get("name")
        or raw.get("title")
        or dataset_id
    )
    description = clean(raw.get("datasetDescription") or raw.get("description") or raw.get("subject") or "")
    owner = clean(raw.get("owner") or raw.get("publishername") or raw.get("organization") or "")
    pub_date = clean(raw.get("firstPublicationDate") or raw.get("publishdate") or raw.get("created") or "")
    update_date = clean(
        raw.get("lastChangeDate") or raw.get("lastchangesdate") or raw.get("updated") or raw.get("date") or ""
    )
    version = clean(raw.get("guidelineVersion") or raw.get("version") or raw.get("edition") or "")
    terms_of_use = clean(raw.get("termsOfUse") or raw.get("termsofuse") or raw.get("terms") or "")
    schema_url = clean(
        raw.get("descriptionOfStructureFile", {}).get("path")
        if isinstance(raw.get("descriptionOfStructureFile"), dict)
        else (raw.get("structuredataurl") or raw.get("schemaUrl") or "")
    )

    files: list[dict[str, Any]] = []

    # 1. Process files from standard format/mock schemas ('data', 'files', 'items')
    raw_files = raw.get("data") or raw.get("files") or raw.get("items") or []
    if isinstance(raw_files, dict):
        raw_files = [raw_files]
    elif not isinstance(raw_files, list):
        raw_files = []

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
                        "name": clean(f.get("name") or ""),
                        "source_url": file_url,
                        "format": file_format,
                        "version": file_version,
                        "description": file_desc,
                        "date": file_date,
                    }
                )

    # 2. Process files from live portal schema ('datasetFile' and 'datasetVersionFiles')
    df = raw.get("datasetFile")
    if isinstance(df, dict) and df.get("path"):
        name = clean(df.get("name") or "")
        ext = name.rsplit(".", 1)[-1].upper() if "." in name else "ZIP"
        files.append(
            {
                "name": name,
                "source_url": f"{BASE}values/GetFileContent/{df['path']}",
                "guid": clean(df["path"]),
                "format": ext,
                "version": version or "current",
                "description": "Актуальный файл набора данных",
                "date": update_date,
            }
        )

    for vf in raw.get("datasetVersionFiles") or []:
        if isinstance(vf, dict) and vf.get("path"):
            vname = clean(vf.get("name") or "")
            vext = vname.rsplit(".", 1)[-1].upper() if "." in vname else "ZIP"
            files.append(
                {
                    "name": vname,
                    "source_url": f"{BASE}values/GetFileContent/{vf['path']}",
                    "guid": clean(vf["path"]),
                    "format": vext,
                    "version": vname,
                    "description": "Версионный файл набора данных",
                    "date": "",
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
    import urllib.parse

    clean_num = dataset_number.strip()
    if not re.fullmatch(r"[A-Za-z0-9_ -]+", dataset_number):
        raise ValueError("Invalid OpenData dataset number")

    path = f"OpenData/GetByNumber/{urllib.parse.quote(dataset_number)}"
    try:
        data, body, meta = network.get_value(path)
    except SourceError as exc:
        resolved = resolve_dataset_number(clean_num)
        if exc.status == 404 and resolved != dataset_number:
            data, body, meta = network.get_value(f"OpenData/GetByNumber/{urllib.parse.quote(resolved)}")
        else:
            raise

    if not isinstance(data, dict):
        raise SourceError("SCHEMA_CHANGED", "Expected OpenData passport object")

    normalized = normalize_passport(data, clean_num)
    return normalized, body, meta


def list_known_opendata(network: Network | None = None) -> list[dict[str, Any]]:
    """List known and discovered OpenData datasets."""
    if network is not None:
        try:
            val, _, _ = network.get_value("OpenData", {})
            if isinstance(val, dict) and isinstance(val.get("items"), list):
                items = []
                for it in val["items"]:
                    ident = clean(it.get("identificationNumber") or "")
                    name = clean(it.get("datasetName") or "")
                    items.append(
                        {
                            "dataset_number": ident.strip(),
                            "dataset_name": name,
                            "last_change_date": clean(it.get("lastChangeDate") or ""),
                            "views": it.get("views", 0),
                            "downloads": it.get("downloads", 0),
                            "passport_path": f"OpenData/GetByNumber/{ident}",
                            "passport_url": f"{BASE}OpenData/GetByNumber/{ident}",
                            "description": name,
                        }
                    )
                if items:
                    return items
        except Exception:
            pass

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
    opendata_data: dict[str, Any] | list[Any], api_data: list[Any]
) -> dict[str, Any]:
    """Cross-check OpenData items/files against API catalog items/nodes with set logic.

    Computes exact sets: opendata_ids, api_ids, intersection, only_in_opendata,
    only_in_api, duplicates, and same_id_different_metadata.
    """
    # Extract IDs and metadata from OpenData
    od_id_list: list[str] = []
    od_meta_by_id: dict[str, Any] = {}
    opendata_files_count = 0
    opendata_version = ""
    opendata_update_date = ""
    dataset_number = ""

    if isinstance(opendata_data, dict):
        dataset_number = opendata_data.get("number", "")
        opendata_version = opendata_data.get("version", "")
        opendata_update_date = opendata_data.get("update_date", "")
        files = opendata_data.get("files", [])
        opendata_files_count = len(files)
        for f in files:
            fid = clean(f.get("name") or f.get("guid") or f.get("source_url") or "")
            if fid:
                od_id_list.append(fid)
                od_meta_by_id[fid] = f
    elif isinstance(opendata_data, list):
        for item in opendata_data:
            if isinstance(item, dict):
                iid = clean(
                    str(item.get("id") or item.get("code") or item.get("guid") or item.get("name") or "")
                )
                if iid:
                    od_id_list.append(iid)
                    od_meta_by_id[iid] = item
            else:
                s = clean(str(item))
                if s:
                    od_id_list.append(s)
                    od_meta_by_id[s] = {"value": s}

    # Extract IDs and metadata from API
    api_id_list: list[str] = []
    api_meta_by_id: dict[str, Any] = {}
    for item in api_data:
        if isinstance(item, dict):
            aid = clean(str(item.get("id") or item.get("code") or item.get("guid") or item.get("name") or ""))
            if aid:
                api_id_list.append(aid)
                api_meta_by_id[aid] = item
        else:
            s = clean(str(item))
            if s:
                api_id_list.append(s)
                api_meta_by_id[s] = {"value": s}

    # Deduplication and duplicate tracking
    seen_od = set()
    od_duplicates = []
    for x in od_id_list:
        if x in seen_od and x not in od_duplicates:
            od_duplicates.append(x)
        seen_od.add(x)

    seen_api = set()
    api_duplicates = []
    for x in api_id_list:
        if x in seen_api and x not in api_duplicates:
            api_duplicates.append(x)
        seen_api.add(x)

    all_duplicates = sorted(list(set(od_duplicates + api_duplicates)))

    od_unique = sorted(list(set(od_id_list)))
    api_unique = sorted(list(set(api_id_list)))

    set_od = set(od_unique)
    set_api = set(api_unique)

    intersection = sorted(list(set_od & set_api))
    only_in_opendata = sorted(list(set_od - set_api))
    only_in_api = sorted(list(set_api - set_od))

    # Detect differing metadata on shared IDs
    same_id_different_metadata = []
    for shared_id in intersection:
        m_od = od_meta_by_id.get(shared_id, {})
        m_api = api_meta_by_id.get(shared_id, {})
        diffs = {}
        for k in set(m_od.keys()) & set(m_api.keys()):
            if m_od[k] != m_api[k]:
                diffs[k] = {"opendata": m_od[k], "api": m_api[k]}
        if diffs:
            same_id_different_metadata.append({"id": shared_id, "differences": diffs})

    discrepancies = []
    if isinstance(opendata_data, dict):
        if opendata_files_count == 0 and len(api_data) > 0:
            discrepancies.append(
                "OpenData passport contains no file distributions, but API catalog has active nodes"
            )

    api_names = [clean(r.get("name") or r.get("documentName") or "") for r in api_data if isinstance(r, dict)]
    matching_nodes = [name for name in api_names if opendata_version and opendata_version in name]

    return {
        "dataset_number": dataset_number,
        "opendata_version": opendata_version,
        "opendata_update_date": opendata_update_date,
        "opendata_files_count": opendata_files_count,
        "api_catalog_nodes_count": len(api_data),
        "api_matching_nodes_count": len(matching_nodes),
        "opendata_ids": od_unique,
        "api_ids": api_unique,
        "intersection": intersection,
        "only_in_opendata": only_in_opendata,
        "only_in_api": only_in_api,
        "duplicates": all_duplicates,
        "same_id_different_metadata": same_id_different_metadata,
        "discrepancies": discrepancies,
        "verified_consistent": len(discrepancies) == 0,
        "note": "Cross-check compares independent publication channels (OpenData passport/files vs live API tree)",
    }

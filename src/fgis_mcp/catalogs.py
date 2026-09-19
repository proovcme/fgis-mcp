"""Public portal adapters discovered from its own UI. No guessed all-FSNB prefix scan.

Original files, technical parts and raw tables stay available even when a dedicated
normalized schema has not been implemented. Access limits are part of the result.
"""

import uuid

from .network import SourceError

# name: (document type, base kind). Values are published UI enums, not inferred editions.
LEGAL = {
    "fsnb2022": (10, 100),
    "fsnb2020": (10, 10),
    "other_norms": (10, 20),
    "enlarged": (10, 30),
    "indices": (10, 40),
    "methodologies": (10, 50),
    "other_methodologies": (10, 60),
    "npa": (20, 70),
    "letters": (20, 80),
    "other_npa": (20, 90),
}
PIR = {
    "pir_methods": "PirMethods",
    "pir_surveys": "SurveyWorkSn",
    "pir_design": "SurveyDesignSn",
    "pir_indices": "PirIndex",
    "pir_examples": "PirCalculationExamples",
    "pir_archive_methods": "PirArchive/Methods",
    "pir_archive_norms": "PirArchive/Sn",
}
PERIOD_PIR = {"pir_surveys", "pir_design", "pir_indices"}


def document_tasks(source, value):
    if not value or str(value) == "00000000-0000-0000-0000-000000000000":
        return []
    return [{"kind": kind, "source": source, "guid": guid(value)} for kind in ("document", "file")]


def document_refs(row, source):
    """References accepted by the online document tools, distinct from catalogue-node GUIDs."""
    candidates = [row.get("normLegalDocPublishedGuid"), row.get("documentLinkGuid"), row.get("frsnDocGuid")]
    has_document = any(value and str(value) != "00000000-0000-0000-0000-000000000000" for value in candidates)
    if not has_document and (row.get("filePath") or source in PIR):
        candidates.append(row.get("guid"))
    refs = []
    for value in candidates:
        if value and str(value) != "00000000-0000-0000-0000-000000000000":
            ref = {"source": "normative", "document_guid": guid(value)}
            if ref not in refs:
                refs.append(ref)
    if row.get("approvingActGuid") and str(row["approvingActGuid"]) != "00000000-0000-0000-0000-000000000000":
        refs.append(
            {
                "source": "normative",
                "document_guid": guid(row["approvingActGuid"]),
                "relation": "approving_act",
            }
        )
    return refs


RESOURCE_TREES = {
    "fsbc_materials": "FsbcMaterials",
    "fsbc_machines": "FsbcMachines",
    "fssc": "FsRegistryPublic/Fssc",
    "fsem": "FsRegistryPublic/Fsem",
}
ALL_SOURCES = [
    *LEGAL,
    *PIR,
    "fer",
    "fssc_freight",
    *RESOURCE_TREES,
    "registry",
    "archive_files",
    "salaries",
    "split_forms",
    "current_prices",
    "opendata",
]


def inventory():
    from .coverage import source_capability

    return {
        "sources": ALL_SOURCES,
        "scope": "Public estimating data, not personal accounts or price monitoring submissions",
        "coverage_matrix": {s: source_capability(s) for s in ALL_SOURCES},
        "known_limits": {
            "ter": "Registry sections 6 and 7; external links are retained, not fetched automatically",
            "coefficients": "Contained in full technical parts and methodological documents; no rule inference",
            "archive_files": "Catalogue only; portal download requires interactive CAPTCHA",
            "split_forms": "Explicit region/zone/period or all-period discovery; potentially very large",
            "opendata": "Official passports and dataset file distributions (FSNB-2022 / FSNB-2020)",
        },
    }


def guid(value):
    try:
        parsed = uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueError("Expected a source GUID") from exc
    if not parsed.int:
        raise ValueError("Empty source GUID")
    return str(parsed)


def task_roots(sources, include_archive=False, all_periods=False, opendata_version=None):
    if sources == ["all_public"]:
        sources = ALL_SOURCES
    if not sources or any(s not in ALL_SOURCES for s in sources):
        raise ValueError("Choose named sources from fgis_sources, or ['all_public']")
    tasks = []
    for source in dict.fromkeys(sources):
        if source == "opendata":
            from . import opendata

            for num in opendata.KNOWN_OPENDATA_DATASETS:
                tasks.append(
                    {
                        "kind": "catalog",
                        "source": "opendata",
                        "dataset_number": num,
                        **({"include_archive": True} if include_archive or opendata_version else {}),
                        **({"opendata_version": str(opendata_version)} if opendata_version else {}),
                    }
                )
            continue
        tasks.append(
            {
                "kind": "catalog",
                "source": source,
                **(
                    {"all_periods": all_periods}
                    if source in {"split_forms", "current_prices"} or source in PERIOD_PIR
                    else {}
                ),
            }
        )
        if include_archive and source in LEGAL:
            tasks.append({"kind": "catalog", "source": source, "archive": True})
    return tasks


def request(task):
    source = task["source"]
    if source == "current_prices" and "stage" in task:
        from . import price_catalogs

        return price_catalogs.request(task)
    if source in LEGAL:
        doc_type, base_kind = LEGAL[source]
        params = {
            "normLegalDocPublishedType": doc_type,
            "normLegalDocBaseKind": base_kind,
            "state": 30 if task.get("archive") else 20,
        }
        if source.startswith("fsnb"):
            params["normLegalDocPublishedTypes"] = [10, 41]
        if task.get("parent"):
            params.update(parentGuid=guid(task["parent"]))
            if "level" in task:
                params["level"] = task["level"]
        return "NormLegalDocPublished", params
    if source in PIR:
        if source in PERIOD_PIR and "period_id" not in task:
            return "PirIndex/Periods", {}
        return PIR[source], {"periodId": task["period_id"]} if "period_id" in task else {}
    if source in {"fer", "fssc_freight"}:
        return ("FerDocumentCompilations" if source == "fer" else "FsscPgDocument"), {
            "parentGuid": guid(task["parent"])
        } if task.get("parent") else {}
    if source in RESOURCE_TREES:
        path = RESOURCE_TREES[source]
        if task.get("parent"):
            return path + ("/children" if source.startswith("fsbc") else ""), {
                "parentId": int(task["parent"])
            }
        return path, {}
    if source == "registry":
        if "section" in task:
            return "FrsnEdition/Estimate/Section", {
                "section": task["section"],
                "take": 100,
                "page": task.get("page", 1),
                "orderBy": "id ASC",
            }
        return "FrsnEdition/section", {}
    if source == "archive_files":
        return "FrsnFileRegistry", {"state": 1, "fsnbType": task.get("fsnb_type", 1)}
    if source == "salaries":
        return (
            ("FrsnWorkerSalary", {"year": task["year"]}) if "year" in task else ("FrsnWorkerSalary/years", {})
        )
    if source in {"split_forms", "current_prices"}:
        if "zone_id" in task:
            return "EstimatedPrice/Periods", {"priceZoneId": task["zone_id"]}
        if "region_id" in task:
            return "EstimatedPrice/PriceZones", {"subjectId": task["region_id"]}
        return "EstimatedPrice/CountrySubjects", {}
    if source == "opendata":
        import urllib.parse

        from .opendata import resolve_dataset_number

        num = task.get("dataset_number", "7707082071-fsnb")
        resolved = resolve_dataset_number(str(num))
        return "OpenData/GetByNumber/" + urllib.parse.quote(resolved), {}
    raise ValueError("Unsupported source")


def items_from(payload, task):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if task["source"] in PIR and "documents" in payload:
            return payload["documents"]
        if task["source"] == "opendata":
            return payload.get("data") or payload.get("files") or [payload]
        for key in (str(task.get("parent", "")), "items", "types"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise SourceError("SCHEMA_CHANGED", "Catalogue response shape differs from the public portal contract")


def children(payload, task):
    source = task["source"]
    if source == "current_prices" and "stage" in task:
        from . import price_catalogs

        return price_catalogs.children(payload, task)
    rows = items_from(payload, task)
    out = []
    if source in LEGAL or source in {"fer", "fssc_freight"}:
        for row in rows:
            if not isinstance(row, dict):
                raise SourceError("SCHEMA_CHANGED", "Invalid catalogue row")
            if row.get("filePath"):
                document_guid = row.get("normLegalDocPublishedGuid")
                if not document_guid or str(document_guid) == "00000000-0000-0000-0000-000000000000":
                    document_guid = row.get("guid")
                if document_guid and str(document_guid) != "00000000-0000-0000-0000-000000000000":
                    out.extend(document_tasks(source, document_guid))
            if row.get("documentLinkGuid"):
                out.extend(document_tasks(source, row["documentLinkGuid"]))
            if row.get("normTableJson") or row.get("fsscPgTableJson"):
                continue
            if row.get("isLeaf") is False:
                child = {**task, "parent": guid(row["guid"])}
                if row.get("level") is not None:
                    child["level"] = row["level"]
                out.append(child)
    elif source in PIR:
        if source in PERIOD_PIR and "period_id" not in task:
            chosen = (
                rows
                if task.get("all_periods")
                else [r for r in rows if r["value"] == payload.get("activePeriodId")]
            )
            if rows and not chosen:
                raise SourceError("SCHEMA_CHANGED", "PIR active period is missing")
            out.extend({**task, "period_id": r["value"]} for r in chosen)
        else:
            for row in rows:
                out.extend(document_tasks(source, row.get("frsnDocGuid") or row.get("guid")))
                out.extend(document_tasks(source, row.get("approvingActGuid")))
    elif source in RESOURCE_TREES:
        if isinstance(payload, dict) and payload.get("guid"):
            out.append({"kind": "resource_document", "source": source})
        for row in rows:
            if row.get("items") is not None:
                out.extend({**task, "parent": r["id"]} for r in row["items"] if r.get("isLeaf") is False)
            elif row.get("isLeaf") is False:
                out.append({**task, "parent": row["id"]})
    elif source == "registry":
        if "section" not in task:
            for row in rows:
                for section in row.get("items") or [row]:
                    out.append({**task, "section": section["id"], "page": 1})
        else:
            total = payload.get("totalCount")
            expected = (
                min(100, max(0, total - (task.get("page", 1) - 1) * 100)) if isinstance(total, int) else -1
            )
            if len(rows) != expected:
                raise SourceError("PAGINATION_MISMATCH", "Registry page/count mismatch")
            if task.get("page", 1) * 100 < total:
                out.append({**task, "page": task.get("page", 1) + 1})
    elif source == "archive_files" and "fsnb_type" not in task:
        out.append({**task, "fsnb_type": 2})
    elif source == "salaries" and "year" not in task:
        for year in rows:
            value = year.get("id", year.get("year")) if isinstance(year, dict) else year
            out.append({**task, "year": int(value)})
    elif source in {"split_forms", "current_prices"}:
        if "zone_id" in task:
            if not all(isinstance(r, dict) and "id" in r and "name" in r for r in rows):
                raise SourceError("SCHEMA_CHANGED", "Invalid periods catalogue")
            import re

            def period_order(r):
                text = r["name"]
                year = re.search(r"(?:19|20)\d{2}", text)
                quarter = re.search(r"([1-4])\s*(?:кв|квартал)", text)
                if not year or not quarter:
                    raise SourceError(
                        "SCHEMA_CHANGED", "Cannot order published periods; choose an explicit period"
                    )
                return int(year[0]), int(quarter[1])

            chosen = rows if task.get("all_periods") else sorted(rows, key=period_order, reverse=True)[:1]
            if source == "current_prices":
                out.extend({**task, "period_id": r["id"], "stage": "authorities"} for r in chosen)
            else:
                out.extend(
                    {"kind": "prices", "zone_id": task["zone_id"], "period_id": r["id"]} for r in chosen
                )
        elif "region_id" in task:
            out.extend({**task, "zone_id": r["id"]} for r in rows)
        else:
            out.extend({**task, "region_id": r["id"]} for r in rows)
    elif source == "opendata":
        from . import opendata, opendata_xml

        if "dataset_number" not in task:
            out.extend({**task, "dataset_number": num} for num in opendata.KNOWN_OPENDATA_DATASETS)
        else:
            passport = opendata.normalize_passport(payload, task["dataset_number"])
            all_files = passport.get("files", [])
            target_version = task.get("opendata_version")
            if target_version:
                tv = str(target_version).strip().casefold()
                selected_files = []
                for f in all_files:
                    fname = str(f.get("name", "")).casefold()
                    furl = str(f.get("source_url", "")).casefold()
                    snap = opendata_xml.extract_snapshot_id(fname or furl).casefold()
                    if tv in fname or tv in furl or tv == snap:
                        selected_files.append(f)
                if not selected_files:
                    selected_files = [f for f in all_files if tv in str(f.get("source_url", "")).casefold()]
            elif not task.get("include_archive"):
                selected_files = [f for f in all_files if f.get("is_current")] or all_files[:1]
            else:
                selected_files = all_files
            for f in selected_files:
                out.append(
                    {
                        "kind": "opendata_file",
                        "source": "opendata",
                        "dataset_number": task["dataset_number"],
                        "file_url": f["source_url"],
                        "format": f.get("format", "bin"),
                        "name": f.get("name", ""),
                    }
                )
    return out


def browse(
    network,
    source,
    parent=None,
    level=None,
    archive=False,
    section=None,
    page=1,
    limit=20,
    offset=0,
    source_params=None,
):
    from .service import page as paginate

    if source not in ALL_SOURCES:
        raise ValueError("Choose a source from fgis_sources")
    task = {"kind": "catalog", "source": source, "archive": archive}
    allowed = {
        "region_id",
        "zone_id",
        "period_id",
        "authority_id",
        "stage",
        "group_id",
        "materials",
        "year",
        "fsnb_type",
        "dataset_number",
    }
    for key, value in (source_params or {}).items():
        if key not in allowed:
            raise ValueError("Unknown source parameter")
        if key in {"stage", "dataset_number"}:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
        elif key == "materials":
            if type(value) is not bool:
                raise ValueError("materials must be boolean")
        elif type(value) is not int or value <= 0:
            raise ValueError("Source IDs must be positive integers")
        task[key] = value
    for key, value in (("parent", parent), ("level", level), ("section", section)):
        if value is not None:
            task[key] = value
    if section is not None:
        task["page"] = page
    payload, _, meta = network.get_value(*request(task))
    rows = items_from(payload, task)
    # Large norm/resource JSON remains accessible via download and document tools.
    summaries = []
    for row in rows:
        if isinstance(row, dict):
            item = {k: v for k, v in row.items() if not k.endswith("Json") and k != "fullPublishedText"}
            item["document_refs"] = document_refs(row, source)
            if row.get("urlUrl"):
                url = str(row["urlUrl"])
                is_direct = any(
                    url.lower().endswith(ext) for ext in (".zip", ".rar", ".7z", ".pdf", ".xls", ".xlsx")
                )
                item["external_link_classification"] = {
                    "url": url,
                    "type": "direct_file" if is_direct else "regional_portal",
                    "requires_manual_download": True,
                    "note": "TER external link; download manually and import via fgis_import_manual_file",
                }
            summaries.append(item)
        else:
            summaries.append(row)

    return paginate(summaries, limit, offset) | {
        "source": source,
        "document_refs": [{"source": source}] if source in {"fssc", "fsem"} else [],
        "provenance": meta,
        "upstream_total": payload.get("totalCount") if isinstance(payload, dict) else None,
    }

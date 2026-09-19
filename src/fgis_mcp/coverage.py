"""Multi-dimensional coverage model and completeness verification."""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# Primary lifecycle states
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"
STATUS_MANUAL = "manual"
STATUS_AUTH_REQUIRED = "auth_required"
STATUS_CAPTCHA_REQUIRED = "captcha_required"
STATUS_UNKNOWN = "unknown"

# Verification proof levels
PROOF_COMPLETE_VERIFIED = "complete_verified"
PROOF_COMPLETE_UNVERIFIED = "complete_unverified"
PROOF_BOUNDED = "bounded"
PROOF_PARTIAL = "partial"
PROOF_FAILED = "failed"
PROOF_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceCapability:
    source_id: str
    name_ru: str
    supported: bool = True
    data_types: list[str] = field(default_factory=list)
    has_history: bool = False
    overall_status: str = STATUS_COMPLETE
    discovery_status: str = STATUS_COMPLETE
    metadata_status: str = STATUS_COMPLETE
    content_status: str = STATUS_COMPLETE
    history_status: str = STATUS_COMPLETE
    verification_status: str = PROOF_UNKNOWN
    known_limitations: list[str] = field(default_factory=list)


# Static matrix of known capabilities (descriptive only: supported types, history, limitations)
CAPABILITIES: dict[str, SourceCapability] = {
    "fsnb2022": SourceCapability(
        source_id="fsnb2022",
        name_ru="ФСНБ-2022 (ГЭСН, ГЭСНм, ГЭСНр, ГЭСНп, капремонт)",
        data_types=["norms", "tables", "resources"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_PARTIAL,
        verification_status=PROOF_UNKNOWN,
        known_limitations=[
            "Official tree nodes represent published editions and supplements.",
            "Full national coverage requires exhaustive tree traversal; search queries are not exhaustive.",
        ],
    ),
    "fsnb2020": SourceCapability(
        source_id="fsnb2020",
        name_ru="ФСНБ-2020 (архивные сборники)",
        data_types=["norms", "tables"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=["Archived collections preserved as published."],
    ),
    "fer": SourceCapability(
        source_id="fer",
        name_ru="Федеральные единичные расценки (ФЕР)",
        data_types=["compilations", "rates"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_PARTIAL,
        verification_status=PROOF_UNKNOWN,
        known_limitations=[
            "Compilations and HTML tables preserved with source cells and spans.",
            "Dedicated machine-readable rate fields depend on official table layouts.",
        ],
    ),
    "registry": SourceCapability(
        source_id="registry",
        name_ru="Федеральный реестр сметных нормативов (ФРСН)",
        data_types=["editions", "sections"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=["All 7 sections paginated with totalCount strictly verified."],
    ),
    "ter": SourceCapability(
        source_id="ter",
        name_ru="Территориальные единичные расценки (ТЕР)",
        data_types=["metadata", "external_links"],
        has_history=True,
        overall_status=STATUS_PARTIAL,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_MANUAL,
        history_status=STATUS_PARTIAL,
        verification_status=PROOF_UNKNOWN,
        known_limitations=[
            "Registry sections 6 and 7 provide official entries and external URLs.",
            "External regional portals are not automatically crawled; user can import manual files.",
        ],
    ),
    "split_forms": SourceCapability(
        source_id="split_forms",
        name_ru="Сметные цены строительных ресурсов (Сплит-формы)",
        data_types=["prices", "monitoring"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=[
            "XLSX split books downloaded and parsed losslessly.",
            "By default, downloads latest period of each zone; all periods available via --all-periods.",
        ],
    ),
    "current_prices": SourceCapability(
        source_id="current_prices",
        name_ru="Текущие цены, индексы, зарплаты, перевозки",
        data_types=["indices", "salaries", "freight"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=[
            "Covers 7 freight export services, resource groups, building/direct cost indices and РИМ wages."
        ],
    ),
    "salaries": SourceCapability(
        source_id="salaries",
        name_ru="Оплата труда рабочего первого разряда по годам",
        data_types=["salaries"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=["Annual historical salary tables preserved."],
    ),
    "archive_files": SourceCapability(
        source_id="archive_files",
        name_ru="Файловые архивы баз ФСНБ",
        data_types=["archives"],
        has_history=True,
        overall_status=STATUS_PARTIAL,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_CAPTCHA_REQUIRED,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=[
            "Catalogue metadata is complete and discoverable.",
            "Direct ZIP download is protected by portal interactive CAPTCHA; manual import supported.",
        ],
    ),
    "opendata": SourceCapability(
        source_id="opendata",
        name_ru="Открытые данные Минстроя России (OpenData)",
        data_types=["passports", "fsnb_xml", "fsbc_xml"],
        has_history=True,
        overall_status=STATUS_COMPLETE,
        discovery_status=STATUS_COMPLETE,
        metadata_status=STATUS_COMPLETE,
        content_status=STATUS_COMPLETE,
        history_status=STATUS_COMPLETE,
        verification_status=PROOF_UNKNOWN,
        known_limitations=["Official passports and dataset file distributions."],
    ),
}


def source_capability(source_id: str) -> dict[str, Any]:
    """Retrieve capability descriptor for a source."""
    if source_id in CAPABILITIES:
        return asdict(CAPABILITIES[source_id])
    return {
        "source_id": source_id,
        "name_ru": source_id,
        "supported": False,
        "data_types": [],
        "has_history": False,
        "overall_status": STATUS_UNKNOWN,
        "discovery_status": STATUS_UNKNOWN,
        "metadata_status": STATUS_UNKNOWN,
        "content_status": STATUS_UNKNOWN,
        "history_status": STATUS_UNKNOWN,
        "verification_status": PROOF_UNKNOWN,
        "known_limitations": [],
    }


def evaluate_coverage(
    tasks: list[dict], completed_receipts: list[dict], errors: list[dict]
) -> dict[str, Any]:
    """Evaluate coverage across all tasks and sources with explicit proof calculation."""
    done_set = {json.dumps(s.get("request", {}), sort_keys=True) for s in completed_receipts}

    by_source: dict[str, dict[str, Any]] = {}
    for task in tasks:
        source = task.get(
            "source",
            "split_forms"
            if task.get("kind") == "prices"
            else "norms_search"
            if task.get("kind") == "norms"
            else task.get("kind", "unknown"),
        )
        if source not in by_source:
            cap = source_capability(source)
            by_source[source] = {
                "source_id": source,
                "name_ru": cap.get("name_ru", source),
                "discovered_tasks": 0,
                "succeeded_tasks": 0,
                "failed_tasks": 0,
                "expected_upstream_total": None,
                "received_upstream_items": 0,
                "overall_status": STATUS_UNKNOWN,
                "dimensions": {
                    "discovery": cap.get("discovery_status", STATUS_UNKNOWN),
                    "metadata": cap.get("metadata_status", STATUS_UNKNOWN),
                    "content": cap.get("content_status", STATUS_UNKNOWN),
                    "history": cap.get("history_status", STATUS_UNKNOWN),
                    "verification": PROOF_UNKNOWN,
                },
                "proof": PROOF_UNKNOWN,
                "known_limitations": cap.get("known_limitations", []),
            }

        entry = by_source[source]
        entry["discovered_tasks"] += 1
        key = json.dumps(task, sort_keys=True)
        if key in done_set:
            entry["succeeded_tasks"] += 1
        else:
            # Check if it failed
            if any(json.dumps(err.get("task", {}), sort_keys=True) == key for err in errors):
                entry["failed_tasks"] += 1

    # Update item counts from receipts
    for receipt in completed_receipts:
        req = receipt.get("request", {})
        source = req.get(
            "source",
            "split_forms"
            if req.get("kind") == "prices"
            else "norms_search"
            if req.get("kind") == "norms"
            else req.get("kind", "unknown"),
        )
        if source in by_source:
            entry = by_source[source]
            records = receipt.get("records") or receipt.get("rows") or 0
            entry["received_upstream_items"] += records
            if "total_count" in receipt:
                entry["expected_upstream_total"] = (entry["expected_upstream_total"] or 0) + receipt[
                    "total_count"
                ]

    # Calculate verification proof for each source strictly from traversal evidence
    has_bounded = False
    has_failed = False
    for source, entry in by_source.items():
        disc = entry["discovered_tasks"]
        succ = entry["succeeded_tasks"]
        fails = entry["failed_tasks"]

        if fails > 0:
            entry["proof"] = PROOF_FAILED if succ == 0 else PROOF_PARTIAL
            entry["overall_status"] = "failed" if succ == 0 else STATUS_PARTIAL
            entry["dimensions"]["verification"] = entry["proof"]
            has_failed = True
        elif succ < disc:
            entry["proof"] = PROOF_BOUNDED
            entry["overall_status"] = "bounded"
            entry["dimensions"]["verification"] = PROOF_BOUNDED
            has_bounded = True
        elif disc == succ and disc > 0:
            # Evidence-based verification proof:
            # 1. Any receipt for this source carries explicit proof == PROOF_COMPLETE_VERIFIED or verified_complete == True
            # 2. Upstream total_count is declared and matches total received records with >0 records
            has_verified_evidence = False
            for r in completed_receipts:
                req = r.get("request", {})
                req_src = req.get(
                    "source",
                    "split_forms"
                    if req.get("kind") == "prices"
                    else "norms_search"
                    if req.get("kind") == "norms"
                    else req.get("kind", "unknown"),
                )
                if req_src == source:
                    if (
                        r.get("proof") == PROOF_COMPLETE_VERIFIED
                        or r.get("verified_complete") is True
                        or (
                            isinstance(r.get("proof"), dict)
                            and r.get("proof", {}).get("proof") == PROOF_COMPLETE_VERIFIED
                        )
                    ):
                        has_verified_evidence = True
                        break

            if has_verified_evidence:
                entry["proof"] = PROOF_COMPLETE_VERIFIED
                entry["dimensions"]["verification"] = PROOF_COMPLETE_VERIFIED
            else:
                entry["proof"] = PROOF_COMPLETE_UNVERIFIED
                entry["dimensions"]["verification"] = PROOF_COMPLETE_UNVERIFIED
            entry["overall_status"] = STATUS_COMPLETE
        else:
            entry["proof"] = PROOF_UNKNOWN
            entry["overall_status"] = STATUS_UNKNOWN
            entry["dimensions"]["verification"] = PROOF_UNKNOWN

    if not tasks:
        overall_proof = PROOF_UNKNOWN
    elif errors or has_failed:
        overall_proof = PROOF_PARTIAL
    elif has_bounded:
        overall_proof = PROOF_BOUNDED
    elif all(e["proof"] == PROOF_COMPLETE_VERIFIED for e in by_source.values()):
        overall_proof = PROOF_COMPLETE_VERIFIED
    elif all(e["proof"] in {PROOF_COMPLETE_VERIFIED, PROOF_COMPLETE_UNVERIFIED} for e in by_source.values()):
        overall_proof = PROOF_COMPLETE_UNVERIFIED
    else:
        overall_proof = PROOF_UNKNOWN

    return {
        "sources": by_source,
        "total_discovered_tasks": len(tasks),
        "total_succeeded_tasks": len(done_set),
        "total_failed_tasks": len(errors),
        "all_requested_tasks_succeeded": len(done_set) == len(tasks) and not errors and len(tasks) > 0,
        "verification_proof": overall_proof,
    }


def verify_collection_completeness(
    reported_total: int,
    received_items: list[Any],
    id_key: str = "id",
) -> dict[str, Any]:
    """Verify completeness of an upstream collection using set-based proof.

    Guarantees:
    - totalCount == len(received_items) with duplicates NEVER yields complete_verified.
    - reported_total == 0 yields unknown/empty, NEVER complete_verified.
    - bounded count yields bounded, NEVER complete.
    """
    total_received = len(received_items)
    ids = []
    for item in received_items:
        if isinstance(item, dict):
            val = (
                item.get(id_key)
                if item.get(id_key) is not None
                else (item.get("code") if item.get("code") is not None else item.get("guid"))
            )
            ids.append(str(val) if val is not None else None)
        else:
            ids.append(str(item))

    seen = set()
    duplicates = []
    for x in ids:
        if x is not None and x in seen and x not in duplicates:
            duplicates.append(x)
        seen.add(x)

    unique_count = len(seen)
    has_duplicates = len(duplicates) > 0 or unique_count < total_received

    if reported_total == 0 and total_received == 0:
        proof = PROOF_UNKNOWN
    elif has_duplicates:
        # Crucial invariant: count matching reported_total with duplicates is NOT complete_verified
        proof = PROOF_PARTIAL
    elif total_received == reported_total and unique_count == reported_total:
        proof = PROOF_COMPLETE_VERIFIED
    elif total_received < reported_total:
        proof = PROOF_BOUNDED
    else:
        proof = PROOF_PARTIAL

    return {
        "reported_total": reported_total,
        "received_count": total_received,
        "unique_ids_count": unique_count,
        "duplicate_ids": duplicates,
        "has_duplicates": has_duplicates,
        "proof": proof,
        "is_complete_verified": proof == PROOF_COMPLETE_VERIFIED,
    }

"""Current prices beyond split forms: published indices, salaries and freight exports."""

from .network import SourceError

GRIDS = {
    "building_indices": "IndicesOfChangeEstimatedPrice",
    "direct_cost_indices": "IndicesOfChangeEstimatedPrice",
    "worker_prices": "EstimatedPrice/RimWorkerSalaryRegistry",
}
SERVICES = {
    "TransportationByAuto",
    "LoadWorksByAuto",
    "TransportationByRail",
    "LoadWorksByRail",
    "TransportationByWater",
    "LoadWorksByWaterAir",
    "TransportationByAir",
}


def scope(task):
    params = {"priceZoneId": task["zone_id"], "periodId": task["period_id"]}
    if task.get("authority_id") is not None:
        params["authorityId"] = task["authority_id"]
    return params


def request(task):
    stage = task["stage"]
    params = scope(task)
    if stage == "authorities":
        return "EstimatedPrice/Authorities", params
    if stage == "services":
        return "EstimatedPrice/ServicesSubTabs", params
    if stage == "group_indices":
        params["isMaterials"] = "true" if task["materials"] else "false"
        if "group_id" in task:
            params["groupId"] = task["group_id"]
            return "IndicesForResourcesGroups/GetTreeViewResourcesInGroup", params
        return "IndicesForResourcesGroups/GetTreeViewResourceGroups", params
    if stage in GRIDS:
        params.update(page=task.get("page", 1), take=100)
        if stage == "direct_cost_indices":
            params["HasDirectCostElementType"] = "true"
        return GRIDS[stage], params
    raise ValueError("Unknown price stage")


def children(payload, task):
    stage = task["stage"]
    if stage == "authorities":
        if not isinstance(payload, list):
            raise SourceError("SCHEMA_CHANGED", "Expected price authority list")
        out = []
        for authority in [None, *[r["id"] for r in payload]]:
            base = {**task, "authority_id": authority}
            out.extend({**base, "stage": s} for s in [*GRIDS, "services"])
            out.extend({**base, "stage": "group_indices", "materials": flag} for flag in (True, False))
            # General split forms have a separate all-region adapter; include agency-specific books here.
            if authority is not None:
                out.append(
                    {
                        "kind": "prices",
                        "source": "current_prices",
                        "zone_id": task["zone_id"],
                        "period_id": task["period_id"],
                        "authority_id": authority,
                    }
                )
        return out
    if stage == "group_indices":
        if not isinstance(payload, list):
            raise SourceError("SCHEMA_CHANGED", "Expected published resource index tree")
        if "group_id" in task:
            return []
        return [{**task, "group_id": row["value"]} for row in payload]
    if stage == "services":
        if not isinstance(payload, list):
            raise SourceError("SCHEMA_CHANGED", "Expected public freight tabs")
        out = []
        for row in payload:
            if row.get("enabled"):
                if row["type"] not in SERVICES:
                    raise SourceError("SCHEMA_CHANGED", "Unknown enabled freight service")
                out.append({**task, "kind": "price_attachment", "service": row["type"]})
        return out
    rows = payload.get("items") if isinstance(payload, dict) else None
    total = payload.get("total") if isinstance(payload, dict) else None
    page = task.get("page", 1)
    if not isinstance(rows, list) or not isinstance(total, int):
        raise SourceError("SCHEMA_CHANGED", "Price grid requires items and total")
    expected = min(100, max(0, total - (page - 1) * 100))
    if len(rows) != expected:
        raise SourceError("PAGINATION_MISMATCH", "Price grid page/count mismatch")
    return [{**task, "page": page + 1}] if page * 100 < total else []


def attachment_request(task):
    if task["service"] not in SERVICES:
        raise ValueError("Unknown freight export")
    return "EstimatedPrice/Services/Export/" + task["service"] + "Portal", scope(task)

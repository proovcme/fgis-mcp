from fgis_mcp.coverage import (
    PROOF_BOUNDED,
    PROOF_COMPLETE_VERIFIED,
    PROOF_PARTIAL,
    STATUS_CAPTCHA_REQUIRED,
    STATUS_COMPLETE,
    STATUS_MANUAL,
    evaluate_coverage,
    source_capability,
)


def test_source_capabilities_multi_dimensional():
    ter_cap = source_capability("ter")
    assert ter_cap["content_status"] == STATUS_MANUAL
    assert ter_cap["overall_status"] != STATUS_COMPLETE

    archive_cap = source_capability("archive_files")
    assert archive_cap["content_status"] == STATUS_CAPTCHA_REQUIRED

    registry_cap = source_capability("registry")
    assert registry_cap["verification_status"] == PROOF_COMPLETE_VERIFIED


def test_evaluate_coverage_all_succeeded():
    tasks = [
        {"kind": "catalog", "source": "registry"},
        {"kind": "catalog", "source": "registry", "section": 1, "page": 1},
    ]
    receipts = [
        {"request": tasks[0], "records": 7},
        {"request": tasks[1], "records": 100},
    ]
    errors = []

    res = evaluate_coverage(tasks, receipts, errors)
    assert res["all_requested_tasks_succeeded"]
    assert res["verification_proof"] == PROOF_COMPLETE_VERIFIED
    reg = res["sources"]["registry"]
    assert reg["discovered_tasks"] == 2
    assert reg["succeeded_tasks"] == 2
    assert reg["failed_tasks"] == 0
    assert reg["received_upstream_items"] == 107


def test_evaluate_coverage_with_errors():
    tasks = [
        {"kind": "catalog", "source": "fsnb2022"},
        {"kind": "catalog", "source": "fsnb2022", "parent": "11111111-1111-4111-8111-111111111111"},
    ]
    receipts = [{"request": tasks[0], "records": 10}]
    errors = [{"task": tasks[1], "code": "NETWORK_ERROR", "message": "Failed"}]

    res = evaluate_coverage(tasks, receipts, errors)
    assert not res["all_requested_tasks_succeeded"]
    assert res["verification_proof"] == PROOF_PARTIAL
    fsnb = res["sources"]["fsnb2022"]
    assert fsnb["failed_tasks"] == 1
    assert fsnb["proof"] == PROOF_PARTIAL


def test_evaluate_coverage_bounded_uncompleted():
    tasks = [
        {"kind": "catalog", "source": "fsnb2022"},
        {"kind": "catalog", "source": "fsnb2022", "parent": "11111111-1111-4111-8111-111111111111"},
    ]
    receipts = [{"request": tasks[0], "records": 10}]
    errors = []  # Stopped before finishing, without explicit failure

    res = evaluate_coverage(tasks, receipts, errors)
    assert not res["all_requested_tasks_succeeded"]
    assert res["sources"]["fsnb2022"]["proof"] == PROOF_BOUNDED
    assert res["verification_proof"] == PROOF_BOUNDED


def test_invariants_bounded_never_turns_into_complete():
    # 5 tasks discovered, only 2 succeeded, 0 errors (max_tasks limit hit)
    tasks = [{"kind": "catalog", "source": "fsnb2022", "i": i} for i in range(5)]
    receipts = [{"request": tasks[i], "records": 10} for i in range(2)]
    errors = []

    res = evaluate_coverage(tasks, receipts, errors)
    assert res["verification_proof"] == PROOF_BOUNDED
    assert res["verification_proof"] != PROOF_COMPLETE_VERIFIED
    assert res["verification_proof"] != "complete_unverified"


def test_invariants_error_never_turns_into_empty():
    tasks = [{"kind": "catalog", "source": "fsnb2022", "parent": "11111111-1111-4111-8111-111111111111"}]
    receipts = []
    errors = [{"task": tasks[0], "code": "TIMEOUT", "message": "Gateway timeout"}]

    res = evaluate_coverage(tasks, receipts, errors)
    assert res["total_failed_tasks"] == 1
    assert res["verification_proof"] == PROOF_PARTIAL
    assert res["verification_proof"] != "unknown"


def test_invariants_empty_never_turns_into_complete():
    # Empty task list must never yield complete
    res = evaluate_coverage([], [], [])
    assert not res["all_requested_tasks_succeeded"]
    assert res["verification_proof"] != PROOF_COMPLETE_VERIFIED
    assert res["verification_proof"] != "complete_unverified"


def test_invariants_totalcount_with_duplicates_never_verified():
    from fgis_mcp.coverage import verify_collection_completeness

    # reported_total: 100, received_items: 100, but with a duplicate item id
    items_with_duplicate = [{"id": i} for i in range(99)] + [{"id": 0}]  # 100 items, id 0 duplicated
    assert len(items_with_duplicate) == 100

    proof_res = verify_collection_completeness(
        reported_total=100,
        received_items=items_with_duplicate,
    )
    assert not proof_res["is_complete_verified"]
    assert proof_res["proof"] == PROOF_PARTIAL
    assert proof_res["has_duplicates"]
    assert proof_res["duplicate_ids"] == ["0"]

    # Without duplicates, it should be complete_verified
    unique_items = [{"id": i} for i in range(100)]
    proof_clean = verify_collection_completeness(
        reported_total=100,
        received_items=unique_items,
    )
    assert proof_clean["is_complete_verified"]
    assert proof_clean["proof"] == PROOF_COMPLETE_VERIFIED

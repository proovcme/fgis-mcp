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

import json
from pathlib import Path
from unittest import mock

import jsonschema
import pytest

from scripts.fetchers import common, stackit

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "prices" / "schema.json").read_text())
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stackit_skus.json"
FIXTURE_SKUS = json.loads(FIXTURE_PATH.read_text())


def test_parse_skus_filters_deprecated_and_hidden():
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)
    instance_ids = {inst["id"] for inst in instances}

    # Verify active GA SKUs are included
    assert "g1a.32d" in instance_ids
    assert "t1.1" in instance_ids
    assert "c2i.4" in instance_ids

    # Find the deprecated and hidden SKU IDs from fixture
    for item in FIXTURE_SKUS:
        attrs = item.get("productSpecificAttributes", {})
        flavor = attrs.get("flavor")
        if not flavor:
            continue
        if item.get("deprecated"):
            assert flavor not in instance_ids or any(
                other.get("productSpecificAttributes", {}).get("flavor") == flavor and not other.get("deprecated")
                for other in FIXTURE_SKUS
            )
        if not item.get("priceListVisibility", False):
            # If all items with this flavor are hidden, it should not be in instances
            all_for_flavor = [
                s for s in FIXTURE_SKUS if s.get("productSpecificAttributes", {}).get("flavor") == flavor
            ]
            if all(not s.get("priceListVisibility", False) for s in all_for_flavor):
                assert flavor not in instance_ids


def test_category_mapping_satisfies_schema_enum():
    category_enum = SCHEMA["$defs"]["instance"]["properties"]["category"]["enum"]
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)

    assert len(instances) > 0
    by_id = {inst["id"]: inst for inst in instances}

    # Tiny -> burstable
    assert by_id["t1.1"]["category"] == "burstable"
    # Compute Optimized -> compute
    assert by_id["c2i.4"]["category"] == "compute"
    # Memory Optimized -> memory
    assert by_id["m2a.8d"]["category"] == "memory"
    # General Purpose -> general
    assert by_id["g1a.32d"]["category"] == "general"
    # GPU -> gpu
    assert by_id["n2.14d.g1"]["category"] == "gpu"

    for inst in instances:
        assert inst["category"] in category_enum


def test_hardware_generation_and_architecture():
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)
    by_id = {inst["id"]: inst for inst in instances}

    # AMD gen 1
    assert by_id["g1a.32d"]["hardware"] == "amd-gen1"
    assert by_id["g1a.32d"]["architecture"] == "x86"

    # Intel gen 2
    assert by_id["c2i.4"]["hardware"] == "intel-gen2"
    assert by_id["c2i.4"]["architecture"] == "x86"

    # Helper function check for ARM
    assert stackit._parse_hardware("ARM", "c1a.4") == "arm-gen1"
    assert stackit._parse_category("Compute Optimized Server", "c1a.4") == "compute"


def test_overprovisioning_and_dedicated_flags():
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)
    by_id = {inst["id"]: inst for inst in instances}

    # Tiny server is overprovisioned
    assert by_id["t1.1"]["overprovisioned"] is True
    assert by_id["t1.1"]["dedicated"] is False

    # Standard general purpose is dedicated
    assert by_id["g1a.32d"]["overprovisioned"] is False
    assert by_id["g1a.32d"]["dedicated"] is True


def test_gpu_count_extraction():
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)
    by_id = {inst["id"]: inst for inst in instances}

    assert by_id["n2.14d.g1"].get("gpu_count") == 1
    assert "gpu_count" not in by_id["g1a.32d"]

    # Test parser function directly for multi-GPU
    assert stackit._parse_gpu_count("n1.56d.g4") == 4
    assert stackit._parse_gpu_count("g1a.32d") is None


def test_gpu_hardware_uses_family_map():
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)
    by_id = {inst["id"]: inst for inst in instances}

    assert by_id["n2.14d.g1"]["hardware"] == "amd-gen2"
    assert stackit._parse_hardware("GPU", "n3.104d.g8") == "amd-gen2"
    assert stackit._parse_hardware("GPU", "n9.14d.g1") == "unknown"


def test_metro_sku_kept_only_without_single_az_sku():
    instances, _ = stackit.parse_skus(FIXTURE_SKUS)
    ids = [inst["id"] for inst in instances]

    # n1.56d.g4 exists only as a metro SKU in the fixture
    assert "n1.56d.g4" in ids
    # g1a.32d has both; only one instance, from the single-AZ SKU
    assert ids.count("g1a.32d") == 1

    az = next(
        s for s in FIXTURE_SKUS
        if (s.get("productSpecificAttributes") or {}).get("flavor") == "g1a.32d"
        and not s["productSpecificAttributes"].get("metro")
    )
    by_id = {inst["id"]: inst for inst in instances}
    assert by_id["g1a.32d"]["price_hourly"] == round(float(az["price"]["list"]["value"]), 8)


def test_control_plane_cost_extraction():
    _, k8s_cost = stackit.parse_skus(FIXTURE_SKUS)
    assert k8s_cost == 71.71


def test_pagination_handling():
    page1 = {
        "meta": {"hasNextPage": True, "nextCursor": "cursor-token-page2"},
        "data": [
            {
                "id": "SKU_P1",
                "title": "General Purpose Server-g1a.4-EU01",
                "priceListVisibility": True,
                "deprecated": False,
                "price": {"list": {"value": 0.05, "currencyCode": "EUR"}, "monthly": {"value": 36.5}},
                "productSpecificAttributes": {
                    "flavor": "g1a.4",
                    "hardware": "AMD",
                    "vCPU": 4,
                    "ram": 16,
                    "metro": False,
                    "cpuOverprovisioning": False,
                },
                "product": {"name": "Server"},
            }
        ],
    }
    page2 = {
        "meta": {"hasNextPage": False, "nextCursor": None},
        "data": [
            {
                "id": "SKU_P2",
                "title": "Compute Optimized Server-c2i.8-EU01",
                "priceListVisibility": True,
                "deprecated": False,
                "price": {"list": {"value": 0.10, "currencyCode": "EUR"}, "monthly": {"value": 73.0}},
                "productSpecificAttributes": {
                    "flavor": "c2i.8",
                    "hardware": "Intel",
                    "vCPU": 8,
                    "ram": 16,
                    "metro": False,
                    "cpuOverprovisioning": False,
                },
                "product": {"name": "Server"},
            }
        ],
    }

    mock_ctx = mock.Mock(spec=common.Context)
    mock_ctx.http_get_json.side_effect = [page1, page2]

    payload = stackit.fetch(mock_ctx, region="eu01")

    assert mock_ctx.http_get_json.call_count == 2
    assert payload["provider"] == "stackit"
    assert len(payload["instances"]) == 2
    assert payload["instances"][0]["id"] == "c2i.8"
    assert payload["instances"][1]["id"] == "g1a.4"


def test_payload_validates_against_schema():
    instances, k8s_cost = stackit.parse_skus(FIXTURE_SKUS)
    payload = {
        "provider": "stackit",
        "fetched_at": common.now_iso(),
        "source": stackit.BASE_URL,
        "source_url": stackit.BASE_URL,
        "manual": False,
        "instances": instances,
    }
    if k8s_cost is not None:
        payload["control_plane_cost"] = k8s_cost

    jsonschema.validate(payload, SCHEMA)

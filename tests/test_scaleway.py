import json
from pathlib import Path

import pytest

from scripts.fetchers import scaleway

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "scaleway_catalog.json").read_text())


def test_price_conversion_from_units_and_nanos():
    # units=0, nanos=11_000_000 -> 0.011 EUR/hour -> 8.03 EUR/month
    product = {
        "sku": "/compute/vc1l/run",
        "product": "VC1-L",
        "locality": {"zone": "fr-par-1"},
        "price": {"retail_price": {"currency_code": "EUR", "units": 0, "nanos": 11000000}},
        "unit_of_measure": {"unit": "hour", "size": 1},
        "properties": {
            "hardware": {
                "cpu": {"arch": "x64", "type": "Intel Atom C3855", "virtual": {"count": 6}},
                "ram": {"size": 8589934592},
                "storage": {"description": "Dynamic local: 1 x SSD, Block", "total": 0},
            },
            "instance": {"range": "VC1", "offer_id": "VC1L"},
        },
    }

    [inst] = scaleway.parse_catalog([product], "fr-par-1")

    assert inst["price_hourly"] == 0.011
    assert inst["price_monthly"] == 8.03
    assert inst["vcpu"] == 6
    assert inst["ram_gb"] == 8
    assert inst["architecture"] == "x86"
    assert inst["currency"] == "EUR"
    assert inst["location"] == "fr-par-1"
    assert inst["range"] == "VC1"
    assert inst["id"] == "VC1-L"


def test_arm_architecture_is_normalised():
    product = {
        "sku": "/compute/basic2_a2c_4g/run_par1",
        "product": "BASIC2-A2C-4G",
        "locality": {"zone": "fr-par-1"},
        "price": {"retail_price": {"currency_code": "EUR", "units": 1, "nanos": 500000000}},
        "unit_of_measure": {"unit": "hour", "size": 1},
        "properties": {
            "hardware": {
                "cpu": {"arch": "arm64", "type": "Ampere Altra", "virtual": {"count": 8}},
                "ram": {"size": 17179869184},
                "storage": {"description": "Block", "total": 0},
            },
            "instance": {"range": "BASIC2", "offer_id": "BASIC2A2C4G"},
        },
    }

    [inst] = scaleway.parse_catalog([product], "fr-par-1")

    assert inst["architecture"] == "arm64"
    assert inst["price_hourly"] == 1.5   # units=1 + nanos=0.5


def test_zone_filtering_is_effective():
    """Test that only products matching the requested zone are included."""
    products = FIXTURE["products"]
    parsed = scaleway.parse_catalog(products, "fr-par-1")

    # Get the IDs of products in the fixture that are in fr-par-1
    fixture_fr_par_1_ids = {
        p.get("product")
        for p in products
        if p.get("locality", {}).get("zone") == "fr-par-1"
        and p.get("sku", "").startswith("/compute/")
        and p.get("properties", {}).get("hardware")
        and p.get("unit_of_measure", {}).get("unit") == "hour"
    }

    # Get the IDs from parsed output
    parsed_ids = {inst["id"] for inst in parsed}

    # They should match exactly
    assert parsed_ids == fixture_fr_par_1_ids

    # All parsed instances should be in fr-par-1
    assert all(inst["location"] == "fr-par-1" for inst in parsed)


def test_category_mapping():
    """Test that product IDs are categorized correctly."""
    test_cases = [
        ("COMPUTE3-X2C-4G", "compute"),
        ("MEMORY3-X2C-16G", "memory"),
        ("POP2-HC-2C-4G", "compute"),
        ("POP2-HM-2C-16G", "memory"),
        ("POP2-2C-8G", "general"),
        ("BASIC2-A2C-4G", "general"),
        ("DEV1-S", "development"),
        ("PLAY2-PICO", "development"),
        ("STARDUST1-S", "development"),
        ("RENDER-GPU-L4", "gpu"),
        ("BASIC3-PRO-L4", "gpu"),
        ("PRO2-H100", "gpu"),
        ("GP1-2C-8G", "general"),
        ("STANDARD3-2C-8G", "general"),
    ]

    for product_id, expected_category in test_cases:
        actual_category = scaleway._get_category(product_id)
        assert actual_category == expected_category, f"{product_id} should be {expected_category}, got {actual_category}"


def test_compute_products_without_hardware_are_ignored():
    no_hardware = {
        "sku": "/compute/vc1xl/run_ams1",
        "product": "VC1-XL",
        "locality": {"zone": "fr-par-1"},
        "price": {"retail_price": {"currency_code": "EUR", "units": 0, "nanos": 49000}},
        "unit_of_measure": {"unit": "hour", "size": 1},
        "properties": {},
    }
    assert scaleway.parse_catalog([no_hardware], "fr-par-1") == []


def test_products_billed_per_minute_are_ignored():
    minute_billed = {
        "sku": "/compute/b300_sxm_8_288g/run_fr-par-2",
        "product": "B300-SXM-8-288G",
        "locality": {"zone": "fr-par-1"},
        "price": {"retail_price": {"currency_code": "EUR", "units": 0, "nanos": 500000000}},
        "unit_of_measure": {"unit": "minute", "size": 1},
        "properties": {
            "hardware": {
                "cpu": {"arch": "x64", "type": "NVIDIA H100", "virtual": {"count": 8}},
                "ram": {"size": 309237645312},
                "storage": {"total": 0},
            },
        },
    }
    assert scaleway.parse_catalog([minute_billed], "fr-par-1") == []


def test_products_missing_price_or_product_id_are_skipped():
    """Test defensive access: products without price or product ID are skipped."""
    no_product_id = {
        "sku": "/compute/test/run",
        "locality": {"zone": "fr-par-1"},
        "price": {"retail_price": {"currency_code": "EUR", "units": 0, "nanos": 100000}},
        "unit_of_measure": {"unit": "hour", "size": 1},
        "properties": {
            "hardware": {
                "cpu": {"arch": "x64", "virtual": {"count": 2}},
                "ram": {"size": 4294967296},
                "storage": {"total": 0},
            },
        },
    }

    no_price = {
        "sku": "/compute/test/run",
        "product": "TEST",
        "locality": {"zone": "fr-par-1"},
        "unit_of_measure": {"unit": "hour", "size": 1},
        "properties": {
            "hardware": {
                "cpu": {"arch": "x64", "virtual": {"count": 2}},
                "ram": {"size": 4294967296},
                "storage": {"total": 0},
            },
        },
    }

    assert scaleway.parse_catalog([no_product_id], "fr-par-1") == []
    assert scaleway.parse_catalog([no_price], "fr-par-1") == []


def test_fetch_paginates_until_total_count_is_reached():
    calls = []

    def stub(url, *args, **kwargs):
        calls.append(url)
        page = int(url.split("page=")[1].split("&")[0])
        if page == 1:
            return {"products": FIXTURE["products"], "total_count": len(FIXTURE["products"]) * 2}
        return {"products": FIXTURE["products"], "total_count": len(FIXTURE["products"]) * 2}

    from scripts.fetchers import common
    ctx = common.Context(prices_dir=Path("prices"), fx=None, http_get_json=stub)
    payload = scaleway.fetch(ctx)

    assert len(calls) == 2
    assert payload["provider"] == "scaleway"
    assert payload["manual"] is False
    assert payload["source_zone"] == "fr-par-1"

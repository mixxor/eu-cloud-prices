import json
from pathlib import Path

import pytest

from scripts.fetchers import ovh

SPECS = json.loads((Path(__file__).parent.parent / "scripts" / "fetchers" / "data" / "ovh_flavors.json").read_text())


def test_micro_unit_conversion():
    # 1_040_000 micro-units renders as "0.01 EUR" per hour
    addons = [{
        "planCode": "d2-2.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1040000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    instances, unknown = ovh.parse_addons(addons, SPECS)

    [inst] = instances
    assert inst["price_hourly"] == 0.0104
    assert inst["price_monthly"] == round(0.0104 * 730, 2)
    assert inst["currency"] == "EUR"
    assert unknown == []


def test_specs_come_from_the_map():
    addons = [{
        "planCode": "d2-2.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1040000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    [inst], _ = ovh.parse_addons(addons, SPECS)

    assert inst["vcpu"] == SPECS["d2-2"]["vcpu"]
    assert inst["ram_gb"] == SPECS["d2-2"]["ram_gb"]
    assert inst["id"] == "d2-2"


def test_unknown_plan_code_is_reported_not_dropped_silently():
    addons = [{
        "planCode": "zz9-plural-z-alpha.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1000000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    instances, unknown = ovh.parse_addons(addons, SPECS)

    assert instances == []
    assert unknown == ["zz9-plural-z-alpha"]


def test_monthly_postpaid_codes_are_ignored():
    addons = [{
        "planCode": "d2-2.monthly.postpaid",
        "product": "publiccloud-instance",
        "pricings": [{"price": 571000000, "intervalUnit": "month", "capacities": ["consumption"]}],
    }]

    instances, unknown = ovh.parse_addons(addons, SPECS)

    assert instances == []
    assert unknown == []


def test_non_eu_localzone_variants_are_excluded():
    addons = [
        {"planCode": "d2-2.consumption.LZ.AF", "product": "publiccloud-instance",
         "pricings": [{"price": 9300000, "intervalUnit": "hour", "capacities": ["consumption"]}]},
    ]

    instances, _ = ovh.parse_addons(addons, SPECS)

    assert instances == []


def test_non_instance_products_are_ignored():
    addons = [{
        "planCode": "b2-7.option.dc-adp.consumption",
        "product": "publiccloud-instance-option-dc-adp",
        "pricings": [{"price": 1360000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    assert ovh.parse_addons(addons, SPECS) == ([], [])


def test_output_currency_is_eur_not_usd():
    """The DE subsidiary catalog is EUR; the pre-automation file was USD."""
    addons = json.loads((Path(__file__).parent / "fixtures" / "ovh_catalog.json").read_text())["addons"]
    instances, _ = ovh.parse_addons(addons, SPECS)
    assert instances
    assert all(i["currency"] == "EUR" for i in instances)


def test_gpu_metadata_is_preserved():
    """GPU flavor specs carry gpu and gpu_model; they must pass through to output."""
    addons = [{
        "planCode": "t2-le-45.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1000000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    [inst], _ = ovh.parse_addons(addons, SPECS)

    assert inst["id"] == "t2-le-45"
    assert inst["gpu"] == SPECS["t2-le-45"]["gpu"]
    assert inst["gpu_model"] == SPECS["t2-le-45"]["gpu_model"]


def test_gpu_metadata_not_emitted_for_non_gpu_flavors():
    """Non-GPU flavors must not have gpu or gpu_model keys, even if set to 0."""
    addons = [{
        "planCode": "d2-2.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1040000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    [inst], _ = ovh.parse_addons(addons, SPECS)

    assert "gpu" not in inst
    assert "gpu_model" not in inst


def test_metal_flavors_are_not_silently_dropped():
    """Bare-metal flavor codes with dots (e.g., metal.eg-256) must be reported if unknown, not dropped."""
    addons = [{
        "planCode": "metal.eg-256.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1000000, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    instances, unknown = ovh.parse_addons(addons, SPECS)

    # metal.eg-256 is not in SPECS, so it should be reported as unknown, not silently dropped.
    assert instances == []
    assert "metal.eg-256" in unknown


def test_plain_consumption_prefix_wins_over_3az_when_consumption_arrives_second():
    """Plain .consumption rate should win even if .consumption.3AZ arrives first in the addon list."""
    addons = [
        {
            "planCode": "d2-2.consumption.3AZ",
            "product": "publiccloud-instance",
            "pricings": [{"price": 1050000, "intervalUnit": "hour", "capacities": ["consumption"]}],
        },
        {
            "planCode": "d2-2.consumption",
            "product": "publiccloud-instance",
            "pricings": [{"price": 1040000, "intervalUnit": "hour", "capacities": ["consumption"]}],
        },
    ]

    [inst], _ = ovh.parse_addons(addons, SPECS)

    # Should use the .consumption price (1040000 / 1e8 = 0.0104), not the .3AZ price.
    assert inst["price_hourly"] == 0.0104


def test_plain_consumption_prefix_wins_over_3az_when_consumption_arrives_first():
    """Plain .consumption rate should win even if .consumption.3AZ arrives later in the addon list."""
    addons = [
        {
            "planCode": "d2-2.consumption",
            "product": "publiccloud-instance",
            "pricings": [{"price": 1040000, "intervalUnit": "hour", "capacities": ["consumption"]}],
        },
        {
            "planCode": "d2-2.consumption.3AZ",
            "product": "publiccloud-instance",
            "pricings": [{"price": 1050000, "intervalUnit": "hour", "capacities": ["consumption"]}],
        },
    ]

    [inst], _ = ovh.parse_addons(addons, SPECS)

    # Should use the .consumption price (1040000 / 1e8 = 0.0104), not the .3AZ price.
    assert inst["price_hourly"] == 0.0104


def test_micro_unit_conversion_with_divergent_rounding():
    """Test micro-unit conversion with a price that shows different rounding from raw vs rounded hourly."""
    # 1_234_567 micro-units = 0.01234567 EUR/hour
    # Rounded to 4 decimals: 0.0123
    # Monthly from raw: 0.01234567 * 730 = 9.0123... ≈ 9.01
    # Monthly from rounded: 0.0123 * 730 = 8.979 ≈ 8.98
    # These differ, so the test pins the implementation correctly derives from the raw value.
    addons = [{
        "planCode": "d2-2.consumption",
        "product": "publiccloud-instance",
        "pricings": [{"price": 1234567, "intervalUnit": "hour", "capacities": ["consumption"]}],
    }]

    instances, _ = ovh.parse_addons(addons, SPECS)

    [inst] = instances
    assert inst["price_hourly"] == 0.0123
    assert inst["price_monthly"] == round(0.01234567 * 730, 2)
    # This should be 9.01, not 8.98, proving we derived from the unrounded value.
    assert inst["price_monthly"] == 9.01

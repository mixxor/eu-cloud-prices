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


# --- VPS range (second catalog) ---

from scripts.fetchers import common  # noqa: E402

VPS_CATALOG = json.loads((Path(__file__).parent / "fixtures" / "ovh_vps_catalog.json").read_text())
CLOUD_CATALOG = json.loads((Path(__file__).parent / "fixtures" / "ovh_catalog.json").read_text())


def _vps_catalog(**plan_overrides):
    """A deep copy of the recorded catalog with one plan's fields replaced."""
    catalog = json.loads(json.dumps(VPS_CATALOG))
    for plan in catalog["plans"]:
        if plan["planCode"] in plan_overrides:
            plan.update(plan_overrides[plan["planCode"]])
    return catalog


def test_vps_plans_on_sale_are_read_with_specs_from_the_catalog():
    instances, unknown = ovh.parse_vps_plans(VPS_CATALOG)

    assert [(i["id"], i["vcpu"], i["ram_gb"], i["disk_gb"], i["price_monthly"]) for i in instances] == [
        ("vps-2027-model1", 2, 4, 40, 4.49),
        ("vps-2027-model2", 4, 8, 75, 8.49),
        ("vps-2027-model3", 6, 12, 100, 12.24),
        ("vps-2027-model4", 8, 24, 200, 23.49),
    ]
    assert unknown == []


def test_vps_price_is_the_month_without_commitment_not_the_12_month_from_price():
    """ovhcloud.com advertises 'from 7.21' for VPS-2; that row needs a 12-month commitment."""
    by_id = {i["id"]: i for i in ovh.parse_vps_plans(VPS_CATALOG)[0]}

    assert by_id["vps-2027-model2"]["price_monthly"] == 8.49


def test_vps_instances_are_marked_vps_and_carry_no_hourly_price():
    for inst in ovh.parse_vps_plans(VPS_CATALOG)[0]:
        assert inst["category"] == "vps"
        assert inst["price_hourly"] is None
        assert inst["currency"] == "EUR"
        assert inst["architecture"] == "x86"
        assert inst["disk_type"] == "nvme"


def test_vps_local_zone_plans_are_excluded():
    """vps-2027-model1.LZ is on sale too, but prices other hardware in other locations."""
    ids = [i["id"] for i in ovh.parse_vps_plans(VPS_CATALOG)[0]]

    assert "vps-2027-model1.LZ" in [p["planCode"] for p in VPS_CATALOG["plans"]]
    assert not any(i.endswith(".LZ") for i in ids)


def test_vps_generations_no_longer_on_sale_are_ignored_not_reported():
    instances, unknown = ovh.parse_vps_plans(VPS_CATALOG)
    seen = [i["id"] for i in instances] + unknown

    assert "vps-2025-model1" not in seen
    assert "vps-starter-1-2-20" not in seen


def test_vps_plan_on_sale_without_specs_is_reported_not_guessed():
    catalog = _vps_catalog()
    catalog["products"] = [p for p in catalog["products"] if p["name"] != "vps-2027-model3"]

    instances, unknown = ovh.parse_vps_plans(catalog)

    assert "vps-2027-model3" not in [i["id"] for i in instances]
    assert unknown == ["vps-2027-model3"]


def test_vps_plan_without_a_no_commitment_price_is_reported_not_guessed():
    committed_only = [p for p in VPS_CATALOG["plans"][1]["pricings"] if p["mode"] != "default"]
    catalog = _vps_catalog(**{"vps-2027-model2": {"pricings": committed_only}})

    instances, unknown = ovh.parse_vps_plans(catalog)

    assert "vps-2027-model2" not in [i["id"] for i in instances]
    assert unknown == ["vps-2027-model2"]


def _ctx(tmp_path, vps_catalog):
    def fake_get(url, *args, **kwargs):
        return {ovh.BASE_URL: CLOUD_CATALOG, ovh.VPS_URL: vps_catalog}[url]

    return common.Context(prices_dir=tmp_path, http_get_json=fake_get)


def test_fetch_publishes_cloud_flavors_and_vps_in_one_file(tmp_path):
    payload = ovh.fetch(_ctx(tmp_path, VPS_CATALOG))

    categories = {i["id"]: i["category"] for i in payload["instances"]}
    assert categories["vps-2027-model2"] == "vps"
    assert any(c != "vps" for c in categories.values())
    assert payload["vat_included"] is False
    assert payload["provider"] == "ovh"


def test_fetch_output_validates_against_the_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parent.parent / "prices" / "schema.json").read_text())

    jsonschema.validate(ovh.fetch(_ctx(tmp_path, VPS_CATALOG)), schema)


def test_fetch_fails_when_no_vps_plan_is_on_sale(tmp_path):
    """If OVH drops the tag or hides the range, the run must fail instead of publishing a file without VPS."""
    catalog = _vps_catalog()
    for plan in catalog["plans"]:
        plan["blobs"]["tags"] = []

    with pytest.raises(common.FetchError, match="VPS"):
        ovh.fetch(_ctx(tmp_path, catalog))

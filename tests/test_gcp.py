"""GCP fetcher: parses the region tables embedded in cloud.google.com pricing pages (issue #36)."""
import json
from pathlib import Path

import jsonschema
import pytest

from scripts.fetchers import common, fx, gcp

FIXTURE = (Path(__file__).parent / "fixtures" / "gcp_pricing_page.html").read_text()
SCHEMA = json.loads((Path(__file__).parent.parent / "prices" / "schema.json").read_text())
RATES = fx.FxRates(rates={"USD": 1.16}, rate_date="2026-10-27")


def test_only_hourly_tables_of_the_requested_region_are_read():
    parsed = gcp.parse_pricing_html(FIXTURE, "europe-west1")
    # Frankfurt (hourly + monthly) and Belgium's monthly table are ignored
    assert parsed["c4-standard-2"] == {"vcpu": 2, "ram_gb": 7, "usd_hourly": 0.106659164}
    assert parsed["c4-standard-4"]["usd_hourly"] == 0.21765446
    assert set(parsed) == {
        "c4-standard-2", "c4-standard-4", "c4-standard-24",
        "c4a-highmem-1", "e2-medium", "c4-standard-288-metal",
    }


def test_other_region_can_be_selected():
    parsed = gcp.parse_pricing_html(FIXTURE, "europe-west3")
    assert parsed["c4-standard-2"]["usd_hourly"] == 0.114302
    assert len(parsed) == 2


def test_cud_columns_after_the_on_demand_price_are_ignored():
    # c4a-highmem-1 has an extra CUD column ($0.0767) after the on-demand one
    assert gcp.parse_pricing_html(FIXTURE)["c4a-highmem-1"]["usd_hourly"] == 0.0601


def test_conflicting_prices_for_one_machine_type_raise():
    dup = FIXTURE.replace(
        '"Belgium (europe-west1)",[2]]',
        '"Belgium (europe-west1)",[2]],[[],[[[[1,1,null,[null,[null,"c4-standard-2"]]],'
        '[1,1,null,[null,[null,"2"]]],[1,1,null,[null,[null,"7 GiB"]]],'
        '[1,1,null,[null,[null,"$0.2 / 1 hour"]],1]]]],"Belgium (europe-west1)",[3]]',
    )
    with pytest.raises(common.FetchError, match="conflicting prices"):
        gcp.parse_pricing_html(dup)


def test_too_few_machine_types_fail_loudly():
    ctx = common.Context(prices_dir=Path("."), fx=RATES, http_get_text=lambda url, *a, **k: FIXTURE)
    with pytest.raises(common.FetchError, match="page structure changed"):
        gcp.fetch(ctx)


def test_fetch_payload_matches_schema(monkeypatch):
    monkeypatch.setattr(gcp, "MIN_INSTANCES", 1)
    seen = []

    def stub(url, *args, **kwargs):
        seen.append(url)
        return FIXTURE if "general-purpose" in url else ""

    payload = gcp.fetch(common.Context(prices_dir=Path("."), fx=RATES, http_get_text=stub))

    assert [u.split("/pricing/")[1].split("?")[0] for u in seen] == list(gcp.PAGES)
    assert all(common.urllib.parse.urlparse(u).hostname in common.ALLOWED_HOSTS for u in seen)
    jsonschema.validate(payload, SCHEMA)
    by_id = {i["id"]: i for i in payload["instances"]}
    assert by_id["e2-medium"]["category"] == "burstable"
    assert by_id["c4-standard-288-metal"]["category"] == "metal"
    assert by_id["c4a-highmem-1"]["category"] == "memory"
    assert by_id["c4a-highmem-1"]["architecture"] == "arm64"
    assert by_id["c4-standard-2"]["price_hourly"] == round(0.106659164 * round(1 / 1.16, 4), 4)
    assert payload["source_region"] == "europe-west1"

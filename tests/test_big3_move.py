"""The move must not change parsing behaviour, only provenance fields.

Also guards two regressions found in code review of the move:

  - The EUR/USD rate must be rounded to 4 decimals *before* it is used in
    price arithmetic, exactly as ``scrape.py:62`` did
    (``eur_per_usd = round(1.0 / usd_per_eur, 4)``). A naive port that reads
    ``ctx.fx.eur_per("USD")`` at full precision and rounds only the reported
    ``eur_usd_rate`` field silently drifts most AWS/GCP prices by up to
    EUR 0.0001/hour (bigger on large instances) versus the legacy script.

  - ``azure.fetch()`` must not require ``ctx.fx``. Azure retail prices are
    already EUR-denominated at source; ``scrape_azure()`` in the legacy
    script took no rate argument at all, so an ECB outage never blocked an
    Azure refresh. The fetcher must preserve that availability property.
"""
import json
from pathlib import Path

import pytest

from scripts.fetchers import aws, azure, common, fx, gcp

PRICES = Path(__file__).parent.parent / "prices"

_BASELINE_RATES = fx.FxRates(rates={"USD": 1.1534}, rate_date="2026-08-13")

# A rate deliberately chosen so full-precision arithmetic and 4dp
# rounded-input arithmetic disagree on the final rounded price. ECB
# USD-per-EUR = 1.161700000507779 gives eur_per("USD") == 0.860807437 at
# full precision, which rounds to 0.8608 — the same 4dp value the legacy
# script would have used as its *input* to every multiplication.
_TRUE_RATE = 0.860807437
_ROUNDED_RATE = round(_TRUE_RATE, 4)
assert _ROUNDED_RATE == 0.8608
_DIVERGING_RATES = fx.FxRates(rates={"USD": 1.0 / _TRUE_RATE}, rate_date="2026-08-14")


# ---------------------------------------------------------------------------
# Basic shape / wiring checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", [aws, gcp, azure])
def test_entry_point_is_named_fetch(module):
    assert callable(module.fetch)


@pytest.mark.parametrize("name", ["aws", "gcp", "azure"])
def test_existing_file_still_parses_and_has_instances(name):
    data = json.loads((PRICES / f"{name}.json").read_text())
    assert len(data["instances"]) > 0


def test_fx_block_is_json_serialisable():
    rates = fx.FxRates(rates={"USD": 1.1534}, rate_date="2026-08-13")
    json.dumps(rates.block(("USD",)))


# ---------------------------------------------------------------------------
# Mocked end-to-end fetch() runs — small synthetic payloads shaped like each
# provider's real API response, run through the real fetch() function so a
# renamed variable or a misplaced field is actually exercised, not just
# imported.
# ---------------------------------------------------------------------------

_AWS_PAYLOAD = {
    "regions": {
        "EU (Frankfurt)": {
            "c8i large EU Frankfurt Linux": {
                "price": "3.864",
                "Instance Type": "c8i.large",
                "vCPU": "2",
                "Memory": "4 GiB",
            },
            "m6g large EU Frankfurt Linux": {
                "price": "0.15",
                "Instance Type": "m6g.large",
                "vCPU": "2",
                "Memory": "8 GiB",
            },
        }
    }
}


def _aws_stub(url, *args, **kwargs):
    assert "b0.p.awsstatic.com" in url
    return _AWS_PAYLOAD


_GCP_PAYLOAD = {
    "gcp_price_list": {
        "CP-COMPUTEENGINE-VMIMAGE-C2-STANDARD-4": {
            # 8.00 chosen (like the AWS 3.864 fixture) so full-precision vs
            # 4dp-rounded-rate arithmetic disagree on the final rounded price.
            "europe-west1": 8.00,
            "cores": "4",
            "memory": "16",
        },
        "CP-COMPUTEENGINE-VMIMAGE-N2-STANDARD-2": {
            "europe-west1": 0.10,
            "cores": "2",
            "memory": "8",
        },
    }
}


def _gcp_stub(url, *args, **kwargs):
    assert "www.gstatic.com" in url
    return _GCP_PAYLOAD


_AZURE_SPECS_PAYLOAD = {"offers": {"linux-d4sv3-standard": {"cores": 4, "ram": 16}}}
_AZURE_PRICES_PAYLOAD = {
    "Items": [
        {
            "skuName": "D4s v3",
            "armSkuName": "Standard_D4s_v3",
            "productName": "Virtual Machines Dsv3 Series",
            "retailPrice": 0.192,
        }
    ],
    "NextPageLink": None,
}


def _azure_stub(url, *args, **kwargs):
    if "azure.microsoft.com" in url:
        return _AZURE_SPECS_PAYLOAD
    assert "prices.azure.com" in url
    return _AZURE_PRICES_PAYLOAD


def test_aws_fetch_rounds_the_rate_before_price_arithmetic():
    """3.864 (USD) * 0.860807437 (full precision) rounds to 3.3262.
    3.864 (USD) * 0.8608 (4dp-rounded, matching scrape.py:62) rounds to
    3.3261. Only the second is correct parity with the legacy script.
    """
    ctx = common.Context(prices_dir=PRICES, fx=_DIVERGING_RATES, http_get_json=_aws_stub)
    payload = aws.fetch(ctx)
    c8i = next(i for i in payload["instances"] if i["id"] == "c8i.large")

    bug_value = round(3.864 * _TRUE_RATE, 4)
    correct_value = round(3.864 * _ROUNDED_RATE, 4)
    assert bug_value != correct_value, "fixture rate must make the two diverge"

    assert payload["eur_usd_rate"] == _ROUNDED_RATE
    assert c8i["price_hourly"] == correct_value
    assert c8i["price_hourly"] != bug_value


def test_gcp_fetch_rounds_the_rate_before_price_arithmetic():
    ctx = common.Context(prices_dir=PRICES, fx=_DIVERGING_RATES, http_get_json=_gcp_stub)
    payload = gcp.fetch(ctx)
    c2 = next(i for i in payload["instances"] if i["id"] == "c2-standard-4")

    bug_value = round(8.00 * _TRUE_RATE, 4)
    correct_value = round(8.00 * _ROUNDED_RATE, 4)
    assert bug_value != correct_value, "fixture rate must make the two diverge"

    assert payload["eur_usd_rate"] == _ROUNDED_RATE
    assert c2["price_hourly"] == correct_value
    assert c2["price_hourly"] != bug_value


def test_aws_fetch_end_to_end_shape():
    ctx = common.Context(prices_dir=PRICES, fx=_BASELINE_RATES, http_get_json=_aws_stub)
    payload = aws.fetch(ctx)
    assert payload["provider"] == "aws"
    assert len(payload["instances"]) == 2
    assert payload["manual"] is False
    assert "fetched_at" in payload
    assert payload["fx"]["rates"]["USD"] == 1.1534


def test_gcp_fetch_end_to_end_shape():
    ctx = common.Context(prices_dir=PRICES, fx=_BASELINE_RATES, http_get_json=_gcp_stub)
    payload = gcp.fetch(ctx)
    assert payload["provider"] == "gcp"
    assert len(payload["instances"]) == 2
    assert payload["manual"] is False
    assert "fetched_at" in payload


def test_azure_fetch_end_to_end_shape_and_no_eur_usd_rate_key():
    """Replaces a prior version of this test that only read the committed
    prices/azure.json and never called azure.fetch() — that version stayed
    green even if fetch() started adding an eur_usd_rate key, because the
    committed file simply doesn't have one. This calls the real fetch().
    """
    ctx = common.Context(prices_dir=PRICES, fx=_BASELINE_RATES, http_get_json=_azure_stub)
    payload = azure.fetch(ctx)
    assert payload["provider"] == "azure"
    assert len(payload["instances"]) == 1
    assert "eur_usd_rate" not in payload
    assert payload["manual"] is False
    assert "fetched_at" in payload


def test_azure_fetch_never_attaches_an_fx_block_even_when_a_rate_is_available():
    """Azure retail prices are EUR at source and are never multiplied by any
    rate. A prior version of fetch() attached ctx.fx.block(("USD",)) anyway
    "for provenance" whenever a rate happened to be available, which made
    the plausibility gate treat Azure as USD-derived: it FX-netted prices
    that were never converted, misreported "N instances repriced" on every
    ECB rate move, and used fx.rate_date to defeat the fetched_at-only
    revert every week. Azure must carry no fx block at all, even when
    ctx.fx is present and populated (unlike test_azure_fetch_tolerates_
    missing_fx below, which covers ctx.fx being None).
    """
    ctx = common.Context(prices_dir=PRICES, fx=_BASELINE_RATES, http_get_json=_azure_stub)
    payload = azure.fetch(ctx)
    assert "fx" not in payload


def test_azure_fetch_tolerates_missing_fx():
    """Azure prices are EUR at source and never needed a rate; scrape_azure()
    in the legacy script took no rate argument at all. fetch() must not fail
    for want of ECB data it does not use, and never emits an fx key.
    """
    ctx = common.Context(prices_dir=PRICES, fx=None, http_get_json=_azure_stub)
    payload = azure.fetch(ctx)
    assert len(payload["instances"]) == 1
    assert "fx" not in payload
    assert "eur_usd_rate" not in payload

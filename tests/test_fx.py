import json
from pathlib import Path

import pytest

from scripts.fetchers import common, fx

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ecb_rates.json").read_text())


def _stub(_url, *args, **kwargs):
    return FIXTURE


def test_fetch_rates_parses_usd_and_date():
    rates = fx.fetch_rates(http_get_json=_stub)
    # Verify actual fixture values to catch currency relabeling bugs (e.g. off-by-one index)
    assert rates.rates["USD"] == pytest.approx(1.1534)
    assert rates.rates["CHF"] == pytest.approx(0.9373)
    assert len(rates.rate_date) == 10 and rates.rate_date[4] == "-"


def test_fetch_rates_parses_every_requested_currency():
    rates = fx.fetch_rates(http_get_json=_stub)
    for code in ("USD", "CHF", "GBP", "SEK", "DKK", "NOK", "PLN", "CZK"):
        assert code in rates.rates, f"missing {code}"


def test_fetch_rates_raises_on_missing_required_currency_series():
    """USD is the only entry in REQUIRED_CURRENCIES (it's the only one any
    fetcher consumes), so a response missing its series must still raise.
    """
    def incomplete_stub(_url, *args, **kwargs):
        # Deep copy the fixture and remove the USD series (key "0:7:0:0:0",
        # CURRENCY index 7 per the fixture's dimension values).
        response = json.loads(json.dumps(FIXTURE))
        del response["dataSets"][0]["series"]["0:7:0:0:0"]
        return response

    with pytest.raises(common.FetchError, match="missing rates for.*USD"):
        fx.fetch_rates(http_get_json=incomplete_stub)


def test_fetch_rates_succeeds_when_only_a_non_required_currency_is_missing():
    """Only USD is required. A response missing some other currency's series
    (NOK here) must not raise — narrowing the completeness guard to
    REQUIRED_CURRENCIES is exactly what stops one unrelated missing/renamed
    series from taking down both aws and gcp for no benefit.
    """
    def missing_nok_stub(_url, *args, **kwargs):
        # Deep copy the fixture and remove the NOK series (key "0:4:0:0:0",
        # CURRENCY index 4 per the fixture's dimension values).
        response = json.loads(json.dumps(FIXTURE))
        del response["dataSets"][0]["series"]["0:4:0:0:0"]
        return response

    rates = fx.fetch_rates(http_get_json=missing_nok_stub)
    assert rates.rates["USD"] == pytest.approx(1.1534)
    assert "NOK" not in rates.rates


def test_to_eur_converts_usd_downward_when_eur_is_stronger():
    rates = fx.FxRates(rates={"USD": 1.1534}, rate_date="2026-08-13")
    # 1 EUR = 1.1534 USD, so 100 USD = 86.70 EUR
    assert rates.to_eur(100.0, "USD") == pytest.approx(86.70, abs=0.01)


def test_eur_per_is_the_inverse():
    rates = fx.FxRates(rates={"USD": 1.1534}, rate_date="2026-08-13")
    assert rates.eur_per("USD") == pytest.approx(0.8670, abs=0.0001)


def test_block_shape():
    rates = fx.FxRates(rates={"USD": 1.1534, "CHF": 0.9373}, rate_date="2026-08-13")
    assert rates.block(("USD",)) == {
        "base": "EUR",
        "rate_date": "2026-08-13",
        "rates": {"USD": 1.1534},
        "source": "ECB EXR daily reference rates",
    }


def test_unknown_currency_raises():
    rates = fx.FxRates(rates={"USD": 1.1534}, rate_date="2026-08-13")
    with pytest.raises(common.FetchError, match="JPY"):
        rates.to_eur(10.0, "JPY")


def test_failed_fetch_raises_and_never_returns_a_default():
    def boom(_url, *args, **kwargs):
        raise common.FetchError("ECB down")

    with pytest.raises(common.FetchError):
        fx.fetch_rates(http_get_json=boom)


def test_module_contains_no_hardcoded_fallback_rate():
    source = (Path(fx.__file__)).read_text()
    assert "0.92" not in source, "the hardcoded fallback rate must not return"

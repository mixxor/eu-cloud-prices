"""ECB daily reference exchange rates.

Deliberately has no fallback rate: if it cannot be fetched, the
USD-denominated fetchers fail instead of silently using a stale value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import common

CURRENCIES = ("USD", "CHF", "GBP", "SEK", "DKK", "NOK", "PLN", "CZK")

#: Currencies a fetcher actually consumes today (only aws/gcp, both USD).
#: All eight in CURRENCIES are still requested from the ECB for provenance,
#: but only these must be present for the response to be considered usable.
REQUIRED_CURRENCIES = ("USD",)

ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/EXR/"
    "D." + "+".join(CURRENCIES) + ".EUR.SP00.A"
    "?format=jsondata&detail=dataonly&lastNObservations=1"
)

SOURCE = "ECB EXR daily reference rates"


@dataclass(frozen=True)
class FxRates:
    """Rates expressed as units of ``code`` per 1 EUR."""

    rates: dict[str, float]
    rate_date: str

    def _rate(self, code: str) -> float:
        if code not in self.rates:
            raise common.FetchError(f"no ECB rate for {code}")
        return self.rates[code]

    def to_eur(self, amount: float, code: str) -> float:
        return amount / self._rate(code)

    def eur_per(self, code: str) -> float:
        return 1.0 / self._rate(code)

    def block(self, codes: tuple[str, ...] = ("USD",)) -> dict:
        return {
            "base": "EUR",
            "rate_date": self.rate_date,
            "rates": {c: self._rate(c) for c in codes},
            "source": SOURCE,
        }


def fetch_rates(http_get_json: Callable[..., dict] = common.http_get_json) -> FxRates:
    data = http_get_json(ECB_URL)
    try:
        series_dims = data["structure"]["dimensions"]["series"]
        currency_values = next(d for d in series_dims if d["id"] == "CURRENCY")["values"]
        rate_date = data["structure"]["dimensions"]["observation"][0]["values"][0]["id"]

        rates: dict[str, float] = {}
        for key, series in data["dataSets"][0]["series"].items():
            index = int(key.split(":")[1])
            code = currency_values[index]["id"]
            observation = list(series["observations"].values())[0][0]
            rates[code] = float(observation)
    except (KeyError, IndexError, TypeError, ValueError, StopIteration) as exc:
        raise common.FetchError(f"unexpected ECB response shape: {exc}") from exc

    missing = set(REQUIRED_CURRENCIES) - rates.keys()
    if missing:
        raise common.FetchError(f"ECB response missing rates for {sorted(missing)}")

    return FxRates(rates=rates, rate_date=rate_date)


def save_last_rate(rates: FxRates, path: Path) -> None:
    """Mirror the rate so the plausibility report can state how far it moved."""
    path.write_text(json.dumps(
        {"rate_date": rates.rate_date, "rates": rates.rates, "source": SOURCE},
        indent=2,
    ) + "\n")

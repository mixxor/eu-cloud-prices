import json
from pathlib import Path

import pytest

from scripts import fetch_prices
from scripts.fetchers import common


def test_one_failing_provider_does_not_stop_the_others(tmp_path, monkeypatch):
    def good(ctx):
        return {"provider": "good", "instances": [{"id": "a", "price_monthly": 1.0}], "manual": False}

    def bad(ctx):
        raise common.FetchError("upstream 500")

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"good": good, "bad": bad})
    monkeypatch.setattr(fetch_prices, "USD_PROVIDERS", frozenset())
    # USD_PROVIDERS is empty here, so the fetched rate is never used — but
    # run() always calls fx.fetch_rates() up front regardless of who's being
    # tested, and an unpatched call would hit the live ECB API on every test run.
    monkeypatch.setattr(fetch_prices.fx, "fetch_rates", lambda **_: None)

    result = fetch_prices.run(["good", "bad"], tmp_path, dry_run=False)

    assert result["ok"] == ["good"]
    assert "bad" in result["failed"]
    assert (tmp_path / "good.json").exists()
    assert not (tmp_path / "bad.json").exists()


def test_fx_failure_skips_only_usd_providers(tmp_path, monkeypatch):
    def eur_provider(ctx):
        return {"provider": "eur", "instances": [{"id": "a"}], "manual": False}

    def usd_provider(ctx):
        return {"provider": "usd", "instances": [{"id": "b"}], "manual": False}

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"eur": eur_provider, "usd": usd_provider})
    monkeypatch.setattr(fetch_prices, "USD_PROVIDERS", frozenset({"usd"}))
    monkeypatch.setattr(fetch_prices.fx, "fetch_rates", lambda **_: (_ for _ in ()).throw(common.FetchError("ECB down")))

    result = fetch_prices.run(["eur", "usd"], tmp_path, dry_run=False)

    assert result["ok"] == ["eur"]
    assert "usd" in result["failed"]
    assert "ECB" in result["failed"]["usd"] or "rate" in result["failed"]["usd"].lower()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY",
                        {"x": lambda ctx: {"provider": "x", "instances": [], "manual": False}})
    monkeypatch.setattr(fetch_prices, "USD_PROVIDERS", frozenset())
    # Same live-ECB-call hazard as above: patch it out even though the value
    # is unused with USD_PROVIDERS empty.
    monkeypatch.setattr(fetch_prices.fx, "fetch_rates", lambda **_: None)

    fetch_prices.run(["x"], tmp_path, dry_run=True)

    assert list(tmp_path.iterdir()) == []


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        fetch_prices.main(["--provider", "not-a-provider", "--prices-dir", str(tmp_path)])


def test_summary_file_captures_ok_and_failed_without_scraping_stdout(tmp_path, monkeypatch, capsys):
    """The CI workflow needs the {ok, failed} summary as clean JSON, not
    mixed in with every fetcher's progress prints on stdout. --summary-file
    writes it straight to a file so the workflow can build a failed-provider
    list and a step-summary block from it directly.
    """
    def good(ctx):
        return {"provider": "good", "instances": [{"id": "a"}], "manual": False}

    def bad(ctx):
        raise common.FetchError("upstream 500")

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"good": good, "bad": bad})
    monkeypatch.setattr(fetch_prices, "USD_PROVIDERS", frozenset())
    monkeypatch.setattr(fetch_prices.fx, "fetch_rates", lambda **_: None)

    summary_file = tmp_path / "summary.json"
    rc = fetch_prices.main([
        "--provider", "all",
        "--prices-dir", str(tmp_path),
        "--summary-file", str(summary_file),
    ])

    assert rc == 0  # at least one provider ("good") succeeded
    summary = json.loads(summary_file.read_text())
    assert summary["ok"] == ["good"]
    assert "bad" in summary["failed"]
    # Same JSON must also still be printed to stdout for local/manual runs.
    assert json.dumps(summary, indent=2) in capsys.readouterr().out


def test_usd_provider_skips_with_named_reason_instead_of_attributeerror(tmp_path, monkeypatch):
    """Regression test for the Task 3 defect: aws/gcp call ``ctx.fx.eur_per(...)``
    unconditionally and raise a bare ``AttributeError`` when ``ctx.fx is None``.

    Uses the real (unmocked) ``USD_PROVIDERS`` frozenset — "aws" is a member by
    construction — and a stand-in "aws" fetcher that reproduces that exact
    AttributeError if it is ever actually invoked with a ``None`` fx. It must
    never be invoked: the driver's skip check has to fire first, before any
    REGISTRY function is called, whenever the ECB rate is unavailable. A
    sibling non-USD provider must still run and write its file in the same
    invocation.
    """
    assert "aws" in fetch_prices.USD_PROVIDERS  # relies on the production default, not a monkeypatch

    def aws_stub(ctx):
        # Mirrors aws.fetch's real first line: `ctx.fx.eur_per("USD")`.
        # If the driver's guard is bypassed, this reproduces the bare
        # AttributeError the guard exists to prevent.
        return ctx.fx.eur_per("USD")

    def ovh_stub(ctx):
        return {"provider": "ovh", "instances": [{"id": "a"}], "manual": False}

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"aws": aws_stub, "ovh": ovh_stub})
    monkeypatch.setattr(
        fetch_prices.fx, "fetch_rates",
        lambda **_: (_ for _ in ()).throw(common.FetchError("ECB request failed: 503")),
    )

    result = fetch_prices.run(["aws", "ovh"], tmp_path, dry_run=False)

    assert result["ok"] == ["ovh"]
    assert (tmp_path / "ovh.json").exists()

    assert "aws" in result["failed"]
    assert not (tmp_path / "aws.json").exists()
    reason = result["failed"]["aws"]
    assert "AttributeError" not in reason
    assert "ECB" in reason


def test_write_failure_is_isolated_and_later_provider_still_runs(tmp_path, monkeypatch):
    """A write-time failure must be isolated exactly like a fetch-time failure:
    recorded in ``failed`` for its own provider, with the run continuing to the
    next one — not an uncaught exception escaping ``run()`` entirely.

    Reproduces the reviewer's repro: a corrupt existing price file on disk that
    makes ``common.write_price_file`` (via ``load_existing``) raise
    ``json.JSONDecodeError`` for one provider, with a second, healthy provider
    listed right after it. Before the fix, the count/write path sat outside the
    ``try`` block in ``run()``, so this exception propagated straight out of
    ``run()`` and ``good_after`` never ran.
    """
    (tmp_path / "bad_write.json").write_text("{not valid json")

    def bad_write(ctx):
        return {"provider": "bad_write", "instances": [{"id": "a"}], "manual": False}

    def good_after(ctx):
        return {"provider": "good_after", "instances": [{"id": "b"}], "manual": False}

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"bad_write": bad_write, "good_after": good_after})
    monkeypatch.setattr(fetch_prices, "USD_PROVIDERS", frozenset())
    monkeypatch.setattr(fetch_prices.fx, "fetch_rates", lambda **_: None)

    result = fetch_prices.run(["bad_write", "good_after"], tmp_path, dry_run=False)

    assert "bad_write" in result["failed"]
    assert result["ok"] == ["good_after"]
    assert (tmp_path / "good_after.json").exists()


def test_non_fetcherror_exception_from_a_fetcher_is_still_isolated(tmp_path, monkeypatch):
    """The driver's ``except Exception`` guard exists because a fetcher can
    raise anything — a malformed upstream payload might surface as a plain
    ``KeyError`` or ``ValueError``, not necessarily ``common.FetchError``.
    Narrowing that guard to ``except common.FetchError`` must break this test:
    the ``ValueError`` below would then propagate out of ``run()`` instead of
    landing in ``failed``, and ``good`` would never get a chance to run.
    """
    def bad(ctx):
        raise ValueError("malformed upstream payload")

    def good(ctx):
        return {"provider": "good", "instances": [{"id": "a"}], "manual": False}

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"bad": bad, "good": good})
    monkeypatch.setattr(fetch_prices, "USD_PROVIDERS", frozenset())
    monkeypatch.setattr(fetch_prices.fx, "fetch_rates", lambda **_: None)

    result = fetch_prices.run(["bad", "good"], tmp_path, dry_run=False)

    assert "bad" in result["failed"]
    assert "malformed upstream payload" in result["failed"]["bad"]
    assert result["ok"] == ["good"]
    assert (tmp_path / "good.json").exists()
    assert not (tmp_path / "bad.json").exists()


def test_azure_is_not_gated_on_the_ecb_rate(tmp_path, monkeypatch):
    """Driver-level regression test using the real, unpatched ``USD_PROVIDERS``
    (only ``fetch_rates`` and ``REGISTRY`` are stubbed here — ``USD_PROVIDERS``
    is deliberately left alone). Azure's source prices are already EUR, so it
    must run to completion even when the ECB is unreachable; only aws/gcp
    belong in ``USD_PROVIDERS``.

    This is the exact regression a driver-level test must catch: adding
    "azure" to ``USD_PROVIDERS`` would silently stop publishing Azure prices
    every time the ECB happens to be down, for a provider that never needed a
    rate at all. Confirmed this test fails under that mutation (see the task
    report for the reproduction).
    """
    def azure_stub(ctx):
        assert ctx.fx is None
        return {"provider": "azure", "instances": [{"id": "a"}], "manual": False}

    monkeypatch.setattr(fetch_prices.fetchers, "REGISTRY", {"azure": azure_stub})
    monkeypatch.setattr(
        fetch_prices.fx, "fetch_rates",
        lambda **_: (_ for _ in ()).throw(common.FetchError("ECB down")),
    )

    result = fetch_prices.run(["azure"], tmp_path, dry_run=False)

    assert result["ok"] == ["azure"]
    assert result["failed"] == {}
    assert (tmp_path / "azure.json").exists()

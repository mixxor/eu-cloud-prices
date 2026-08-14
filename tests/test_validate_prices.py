"""Tests for scripts/validate-prices.py's optional file-list argument.

The module lives at a hyphenated path (not a valid Python identifier), so
it's loaded via importlib rather than a normal import - mirroring how the
workflow itself invokes it: as a script, not a package.
"""
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "validate_prices", Path(__file__).parent.parent / "scripts" / "validate-prices.py"
)
vp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vp)


def _valid_payload(provider: str) -> dict:
    return {
        "provider": provider,
        "fetched_at": "2026-08-13T00:00:00Z",
        "manual": False,
        "instances": [{
            "id": "a", "name": "A", "vcpu": 1, "ram_gb": 1, "disk_gb": 10,
            "price_monthly": 5.0, "currency": "EUR",
        }],
    }


def test_default_no_args_validates_every_file_in_prices_dir(capsys):
    """Regression guard: the CI push/PR workflow (validate-prices.yml) still
    calls this with no arguments and expects it to sweep the whole prices/
    directory - that behaviour must survive the new opt-in scoping."""
    with pytest.raises(SystemExit) as exc:
        vp.main([])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Validating" in out and "price files against schema" in out


def test_explicit_files_restrict_validation_to_those_files(tmp_path, capsys):
    """A hand-maintained file elsewhere in prices/ must not be able to block
    a CI run that only names the providers it actually fetched."""
    good = tmp_path / "aws.json"
    good.write_text(json.dumps(_valid_payload("aws")))
    bad = tmp_path / "contabo.json"
    bad.write_text(json.dumps({"provider": "contabo"}))  # missing required keys

    with pytest.raises(SystemExit) as exc:
        vp.main([str(good)])
    assert exc.value.code == 0  # contabo.json was never named, so its errors don't count

    out = capsys.readouterr().out
    assert "aws.json" in out
    assert "contabo.json" not in out


def test_explicit_bad_file_still_fails(tmp_path):
    bad = tmp_path / "contabo.json"
    bad.write_text(json.dumps({"provider": "contabo"}))

    with pytest.raises(SystemExit) as exc:
        vp.main([str(bad)])
    assert exc.value.code == 1


def test_a_missing_named_file_is_a_validation_error_not_a_crash(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit) as exc:
        vp.main([str(missing)])
    assert exc.value.code == 1

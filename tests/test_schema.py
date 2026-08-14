import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "prices" / "schema.json").read_text())

_ALL_AUTOMATED_PROVIDERS = ["aws", "gcp", "azure", "ovh", "scaleway", "atlanticcloud"]
# Space-separated override, e.g. from a CI run that only fetched some
# providers this pass - a hand-maintained or untouched automated-provider
# file elsewhere must not be able to block that run. Unset (the default,
# including every direct `pytest tests/test_schema.py` invocation) keeps
# testing the full list.
_env = os.environ.get("SCHEMA_TEST_PROVIDERS")
PROVIDERS_TO_TEST = _env.split() if _env else _ALL_AUTOMATED_PROVIDERS


def test_schema_describes_the_fx_block():
    assert "fx" in SCHEMA["properties"]
    fx = SCHEMA["properties"]["fx"]
    assert set(fx["properties"]) >= {"base", "rate_date", "rates", "source"}


def test_a_payload_with_an_fx_block_validates():
    payload = {
        "provider": "aws",
        "fetched_at": "2026-08-13T00:00:00Z",
        "manual": False,
        "instances": [],
        "fx": {
            "base": "EUR",
            "rate_date": "2026-08-13",
            "rates": {"USD": 1.1534},
            "source": "ECB EXR daily reference rates",
        },
    }
    jsonschema.validate(payload, SCHEMA)


@pytest.mark.parametrize("name", PROVIDERS_TO_TEST)
def test_every_automated_provider_file_validates(name):
    payload = json.loads((ROOT / "prices" / f"{name}.json").read_text())
    jsonschema.validate(payload, SCHEMA)


# PROVIDERS_TO_TEST is fixed at module import time, so exercising the env
# var's effect on *this* process wouldn't prove anything - it's read once,
# before any test runs. Spawn a subprocess instead, matching how the
# workflow actually sets it.
def test_schema_test_providers_env_var_restricts_collection():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_schema.py", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "SCHEMA_TEST_PROVIDERS": "ovh"},
    )
    assert "test_every_automated_provider_file_validates[ovh]" in result.stdout
    assert "test_every_automated_provider_file_validates[aws]" not in result.stdout
    # The two non-parametrized schema-shape tests are never scoped by this
    # env var - they don't depend on which providers were fetched.
    assert "test_schema_describes_the_fx_block" in result.stdout
    assert "test_a_payload_with_an_fx_block_validates" in result.stdout

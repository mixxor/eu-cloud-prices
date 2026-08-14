import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "prices" / "schema.json").read_text())


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


@pytest.mark.parametrize("name", ["aws", "gcp", "azure", "ovh", "scaleway", "atlanticcloud"])
def test_every_automated_provider_file_validates(name):
    payload = json.loads((ROOT / "prices" / f"{name}.json").read_text())
    jsonschema.validate(payload, SCHEMA)

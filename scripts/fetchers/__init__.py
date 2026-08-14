"""Provider price fetchers.

Each provider module exposes ``fetch(ctx) -> dict`` returning a payload that
conforms to ``prices/schema.json``.
"""

from . import atlanticcloud, aws, azure, ovh, scaleway

# gcp is deliberately absent: its public price JSON was withdrawn and the
# documented successor needs an API key, which this project does not use.
# scripts/fetchers/gcp.py is kept and still tested - only the source URL died,
# so re-registering is a one-line change if a public endpoint reappears.
REGISTRY = {
    "atlanticcloud": atlanticcloud.fetch,
    "aws": aws.fetch,
    "azure": azure.fetch,
    "ovh": ovh.fetch,
    "scaleway": scaleway.fetch,
}

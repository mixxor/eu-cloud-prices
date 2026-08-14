"""Provider price fetchers.

Each provider module exposes ``fetch(ctx) -> dict`` returning a payload that
conforms to ``prices/schema.json``.
"""

from . import atlanticcloud, aws, azure, gcp, ovh, scaleway

REGISTRY = {
    "atlanticcloud": atlanticcloud.fetch,
    "aws": aws.fetch,
    "azure": azure.fetch,
    "gcp": gcp.fetch,
    "ovh": ovh.fetch,
    "scaleway": scaleway.fetch,
}

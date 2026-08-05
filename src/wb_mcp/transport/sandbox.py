"""Sandbox URL mapping for Wildberries API.

When the server runs in sandbox mode (``WB_SANDBOX=1``) we swap the
production host for the matching sandbox host.

Mapping is based on the official sandbox documentation
(https://dev.wildberries.ru/en/sandbox):
  - content-api            -> content-api-sandbox
  - discounts-prices-api   -> discounts-prices-api-sandbox
  - marketplace-api        -> marketplace-api-sandbox
  - supplies-api           -> supplies-api-sandbox
  - advert-api             -> advert-api-sandbox
  - feedbacks-api          -> feedbacks-api-sandbox
  - statistics-api         -> statistics-api-sandbox
"""

from __future__ import annotations

SANDBOX_HOSTS: dict[str, str] = {
    "content-api.wildberries.ru": "content-api-sandbox.wildberries.ru",
    "discounts-prices-api.wildberries.ru": "discounts-prices-api-sandbox.wildberries.ru",
    "marketplace-api.wildberries.ru": "marketplace-api-sandbox.wildberries.ru",
    "supplies-api.wildberries.ru": "supplies-api-sandbox.wildberries.ru",
    "advert-api.wildberries.ru": "advert-api-sandbox.wildberries.ru",
    "feedbacks-api.wildberries.ru": "feedbacks-api-sandbox.wildberries.ru",
    "statistics-api.wildberries.ru": "statistics-api-sandbox.wildberries.ru",
}


def sandbox_url(base_url: str) -> str:
    """Convert a production Wildberries API URL to its sandbox equivalent.

    Args:
        base_url: The production API URL (e.g. ``https://content-api.wildberries.ru``)

    Returns:
        The sandbox API URL (e.g. ``https://content-api-sandbox.wildberries.ru``)
        If the base_url doesn't match any known production domain, returns it unchanged.
    """
    for prod_host, sandbox_host in SANDBOX_HOSTS.items():
        if prod_host in base_url:
            return base_url.replace(prod_host, sandbox_host)
    return base_url
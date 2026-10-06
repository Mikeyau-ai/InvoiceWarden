"""Central registry of every provider, split by category.

Two dropdowns in Settings read from here:
  * Service system   -> :data:`SERVICE_PROVIDERS`
  * Accounting system -> :data:`ACCOUNTING_PROVIDERS`

Add a class to the right list below and it appears in the dropdown, the
Settings tab renders only its credential fields, and the router can route to
it - nothing else to wire.
"""
from __future__ import annotations

from integrations.provider_base import NoneProvider, Provider
from integrations.accounting.myob import MyobProvider
from integrations.accounting.quickbooks import QuickBooksProvider
from integrations.accounting.stubs import ACCOUNTING_STUBS
from integrations.accounting.xero import XeroProvider
from integrations.service.servicem8 import ServiceM8Provider
from integrations.service.stubs import SERVICE_STUBS

# --- service systems (job/field-service) --------------------------------
_SERVICE = [ServiceM8Provider, *SERVICE_STUBS]
SERVICE_PROVIDERS: dict[str, type[Provider]] = {c.key: c for c in _SERVICE}
SERVICE_PROVIDERS["none"] = NoneProvider

# --- accounting systems -----------------------------------------------
_ACCOUNTING = [XeroProvider, MyobProvider, QuickBooksProvider, *ACCOUNTING_STUBS]
ACCOUNTING_PROVIDERS: dict[str, type[Provider]] = {c.key: c for c in _ACCOUNTING}
ACCOUNTING_PROVIDERS["none"] = NoneProvider

ALL_PROVIDERS: dict[str, type[Provider]] = {**SERVICE_PROVIDERS, **ACCOUNTING_PROVIDERS}

#: The accounting side is switched off for now (decided 2026-10-06): Xero, MYOB and
#: QuickBooks are built but untested, and the rest are placeholders. The code stays and the
#: router is unchanged; Settings and Suppliers just don't offer it until it's been tested.
ACCOUNTING_ENABLED = False


def selectable_service_providers(current: str = "") -> dict[str, type[Provider]]:
    """Job systems offered in Settings: the built ones, plus whatever is already chosen
    (so an existing setting never silently disappears from the dropdown)."""
    return {k: c for k, c in SERVICE_PROVIDERS.items()
            if k != "none" and (c.implemented or k == current)}


def service_labels() -> dict[str, str]:
    """{key: label} in dropdown order for the Service system selector."""
    return {k: SERVICE_PROVIDERS[k].label for k in SERVICE_PROVIDERS}


def accounting_labels() -> dict[str, str]:
    """{key: label} in dropdown order for the Accounting system selector."""
    return {k: ACCOUNTING_PROVIDERS[k].label for k in ACCOUNTING_PROVIDERS}


def label_for(key: str) -> str:
    """Display label for any provider key (falls back to the key)."""
    cls = ALL_PROVIDERS.get(key)
    return cls.label if cls else key


def build_service_provider(settings) -> Provider:
    """Instantiate the selected Service system (defaults to ServiceM8)."""
    key = settings.get("service.provider", "servicem8")
    return SERVICE_PROVIDERS.get(key, ServiceM8Provider)(settings)


def build_accounting_provider(settings) -> Provider:
    """Instantiate the selected Accounting system (defaults to None)."""
    key = settings.get("accounting.provider", "none")
    return ACCOUNTING_PROVIDERS.get(key, NoneProvider)(settings)


def build_provider(key: str, settings) -> Provider:
    """Instantiate any provider by key."""
    return ALL_PROVIDERS.get(key, NoneProvider)(settings)

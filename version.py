"""Single source of truth for the application version.

Bump ``APP_VERSION`` and publish a GitHub release (``release.bat``) to ship an
update - frozen builds pick it up via :mod:`core.updater`.
"""
APP_VERSION = "1.0.40"

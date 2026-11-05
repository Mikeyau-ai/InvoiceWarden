"""Single source of truth for the application version.

Bump ``APP_VERSION``, run ``release.bat`` then ``tools/release_upload.py`` to
ship an update - frozen builds pick it up via :mod:`core.updater`.
"""
APP_VERSION = "1.0.43"

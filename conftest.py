"""Suite-wide plugins.

The auth-test dev-mode guard lives in image_auth_guard.py. tests/conftest.py
is a FreeRoute dual-write path and is not the place for this hook.
"""

pytest_plugins = ["image_auth_guard"]

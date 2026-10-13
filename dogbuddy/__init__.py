# DogBuddyCn 鈥?Ubuntu 澶?Agent 鍗忎綔杞欢
# Copyright (C) 2026 DogBuddyCn contributors 鈥?AGPL-3.0
"""DogBuddyCn package root.

`__version__` is the single source of truth for the running version. It is
reported by `/healthz` and `/api/version` and asserted by the self-update
chain (`manage.py update`) after restart 鈥?bump it in the same commit that
ships changes.
"""

__version__ = "0.1.3"

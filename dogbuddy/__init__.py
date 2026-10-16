# DogBuddyCn — Ubuntu 多 Agent 协作软件
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""DogBuddyCn package root.

`__version__` is the single source of truth for the running version. It is
reported by `/healthz` and `/api/version` and asserted by the self-update
chain (`manage.py update`) after restart — bump it in the same commit that
ships changes.
"""

__version__ = "0.10.27"

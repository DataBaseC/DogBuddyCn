# DogBuddyCn — skills loader hook (extension point)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Skills system hook — V1 intentionally not implemented (DEV_PROMPT scope),
but the directory contract and loader entry are reserved here so future skill
packages can drop into <data_dir>/skills/ and be discovered.

Contract (reserved):
    <data_dir>/skills/<skill-name>/SKILL.md   — instructions injected into a
                                                matching window's context
    <data_dir>/skills/<skill-name>/tools.py   — optional `all_tools()` export
                                                registered via ToolRegistry
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("dogbuddy.skills")

SKILLS_DIRNAME = "skills"


def skills_dir(data_dir: str | Path) -> Path:
    p = Path(data_dir) / SKILLS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_skills(data_dir: str | Path) -> list:
    """V1 stub: returns []. Future versions scan skills_dir and register skills."""
    root = skills_dir(data_dir)
    found = sorted(d.name for d in root.iterdir() if d.is_dir())
    if found:
        logger.info("skills present but the skill system is not enabled in V1: %s", found)
    return []

# DogBuddyCn — temporary skills (window-scoped)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Window-scoped skills.

A skill is a short procedure written by an employee for its OWN workstation.
It is injected into that window's system prompt on the next turn. It is not a
global prompt and it cannot be read by another window.

Layout (under <data_dir>/skills/<slug>/):
    skill.json   machine-readable metadata + instructions
    SKILL.md     human-readable copy (for the owner / file manager)

Why window-scoped: a "temporary skill" is a working note ("how I run this
project's tests"), not a team-wide policy. Global memory/project memory already
cover shared rules; letting one employee's ad-hoc note leak into every window
would be both noisy and a prompt-injection surface.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dogbuddy.skills")

SKILLS_DIRNAME = "skills"
SKILL_FILE = "skill.json"
MAX_INSTRUCTIONS = 8000
MAX_PROMPT_CHARS = 6000
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def skills_dir(data_dir: str | Path) -> Path:
    p = Path(data_dir) / SKILLS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-_")
    return s[:48]


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_skills(skills_root: str | Path, window_id: str = "") -> list[dict[str, Any]]:
    """Return active skills visible to `window_id` (plus global ones)."""
    root = Path(skills_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        data = _read(child / SKILL_FILE)
        if not data:
            continue
        instructions = str(data.get("instructions") or "").strip()
        if not instructions:
            continue
        owner = str(data.get("window_id") or "")
        if owner and window_id and owner != window_id:
            continue
        if owner and not window_id:
            continue
        out.append({
            "name": str(data.get("name") or child.name),
            "slug": child.name,
            "description": str(data.get("description") or ""),
            "instructions": instructions[:MAX_INSTRUCTIONS],
            "window_id": owner,
            "created_by": str(data.get("created_by") or ""),
            "created_at": str(data.get("created_at") or ""),
            "path": str(child),
        })
    return out


def create_skill(skills_root: str | Path, name: str, instructions: str,
                 *, window_id: str, description: str = "",
                 actor: str = "") -> dict[str, Any]:
    slug = _slug(name)
    if not _NAME_RE.match(slug):
        raise ValueError("skill 名称只能用小写字母、数字、下划线、连字符，1~48 位")
    text = str(instructions or "").strip()
    if len(text) < 10:
        raise ValueError("skill 内容太短（至少 10 个字符），请写可执行的步骤")
    if len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"skill 内容过长（上限 {MAX_INSTRUCTIONS} 字符）")
    root = Path(skills_root)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name.strip()[:80] or slug,
        "slug": slug,
        "description": str(description or "").strip()[:300],
        "instructions": text,
        "window_id": str(window_id or ""),
        "created_by": str(actor or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "temporary": True,
    }
    tmp = folder / (SKILL_FILE + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(folder / SKILL_FILE)
    (folder / "SKILL.md").write_text(
        f"# {data['name']}\n\n{data['description']}\n\n"
        f"窗口：`{data['window_id'] or 'global'}` · 创建者：`{data['created_by'] or '-'}`\n\n"
        f"{text}\n", encoding="utf-8")
    return data


def delete_skill(skills_root: str | Path, name: str, *, window_id: str) -> bool:
    slug = _slug(name)
    folder = Path(skills_root) / slug
    data = _read(folder / SKILL_FILE)
    if not data:
        return False
    owner = str(data.get("window_id") or "")
    if owner and owner != str(window_id or ""):
        raise ValueError("只能删除本工位创建的 skill")
    shutil.rmtree(folder, ignore_errors=True)
    return True


def format_skills(skills: list[dict[str, Any]],
                  max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Render skills for the system prompt, hard-capped so a huge note can't
    crowd out the actual conversation."""
    if not skills:
        return ""
    chunks = ["## Temporary skills (this workstation only)"]
    used = 0
    for s in skills:
        head = f"### {s.get('name') or 'skill'}"
        if s.get("description"):
            head += f"\n{s['description']}"
        body = str(s.get("instructions") or "")
        block = head + "\n" + body
        if used + len(block) > max_chars:
            remain = max_chars - used
            if remain <= 200:
                break
            block = block[:remain] + "\n…(truncated)"
        chunks.append(block)
        used += len(block)
    return "\n\n".join(chunks)

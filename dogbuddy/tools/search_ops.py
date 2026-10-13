# DogBuddyCn — search tools: glob / grep / web_search / web_fetch (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Search tools.

  glob       — workspace file search (Path.glob, max 200 hits)
  grep       — per-line regex search over workspace text files (max 200 hits)
  web_search — DuckDuckGo HTML endpoint, title+url+snippet, max 10 results
  web_fetch  — GET a http(s) URL and return de-tagged plain text

network tools degrade gracefully: any failure (no httpx, no network, HTTP
error) returns ToolResult.failure("web_* unavailable: ...") — never raises.
"""
from __future__ import annotations

import fnmatch
import html as _html
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.search")

MAX_RESULTS = 200
MAX_WEB_RESULTS = 10
MAX_GREP_FILE_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ------------------------------------------------------------------ helpers


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_in_workspace(ctx: ToolContext, path: Any) -> Path:
    ws = Path(str(ctx.workspace)).resolve()
    raw = str(path if path is not None else ".").strip() or "."
    target = (ws / raw).resolve()
    if target != ws and not target.is_relative_to(ws):
        raise ValueError(f"path escapes workspace, rejected: {raw}")
    return target


def _rel(ctx: ToolContext, p: Path) -> str:
    ws = Path(str(ctx.workspace)).resolve()
    try:
        return p.resolve().relative_to(ws).as_posix()
    except ValueError:
        return p.name


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _html_to_text(page: str) -> str:
    """Remove script/style, turn tags into whitespace, compress blank lines."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode_ddg_url(href: str) -> str:
    """Unwrap DuckDuckGo redirect links (//duckduckgo.com/l/?uddg=<real url>)."""
    href = _html.unescape(href.strip())
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.query:
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
    except Exception:  # noqa: BLE001
        pass
    return href


# ------------------------------------------------------------------- tools


class GlobTool:
    """glob — find files by glob pattern inside the workspace."""

    name = "glob"
    description = (
        "Find workspace paths matching a glob pattern (e.g. '**/*.py'), "
        "optionally rooted at `path`. Returns paths relative to the workspace, "
        "at most 200."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.ts' or 'src/*.json'.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in, relative to the workspace (default '.').",
            },
        },
        "required": ["pattern"],
    }
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pattern = str(args.get("pattern", "") or "").strip()
            if not pattern:
                return ToolResult.failure("pattern must be a non-empty string")
            try:
                base = _resolve_in_workspace(ctx, args.get("path", "."))
            except ValueError as e:
                return ToolResult.failure(str(e))
            if not base.exists():
                return ToolResult.failure(f"path not found: {_rel(ctx, base)}")
            try:
                matches = sorted(base.glob(pattern))
            except (ValueError, OSError) as e:
                return ToolResult.failure(f"invalid glob pattern: {e}")
            rels = [_rel(ctx, m) for m in matches]
            total = len(rels)
            shown = rels[:MAX_RESULTS]
            out = "\n".join(shown) if shown else "(no matches)"
            if total > MAX_RESULTS:
                out += f"\n...[{total - MAX_RESULTS} more matches not shown]"
            return ToolResult.success(out, total=total, returned=len(shown))
        except Exception as e:  # noqa: BLE001
            logger.warning("glob failed", exc_info=True)
            return ToolResult.failure(f"glob failed: {e}")


class GrepTool:
    """grep — regex search over workspace text files."""

    name = "grep"
    description = (
        "Search workspace text files line by line with a Python regex. Results "
        "are 'relpath:lineno:line', at most 200. Use `include` (a filename glob "
        "like '*.py') to restrict which files are searched."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Python regular expression matched against each line.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in, relative to the workspace (default '.').",
            },
            "include": {
                "type": "string",
                "description": "Filename glob filter, e.g. '*.py' (default: all files).",
            },
        },
        "required": ["pattern"],
    }
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pattern = str(args.get("pattern", "") or "")
            if not pattern:
                return ToolResult.failure("pattern must be a non-empty string")
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return ToolResult.failure(f"invalid regex: {e}")
            include = args.get("include")
            include = str(include).strip() if include else None
            try:
                base = _resolve_in_workspace(ctx, args.get("path", "."))
            except ValueError as e:
                return ToolResult.failure(str(e))
            if not base.exists():
                return ToolResult.failure(f"path not found: {_rel(ctx, base)}")

            results: list[str] = []
            truncated = False
            if base.is_file():
                scan_roots = [base]
            else:
                scan_roots = []
                for dirpath, dirnames, filenames in os.walk(base):
                    dirnames[:] = sorted(
                        d for d in dirnames
                        if d not in SKIP_DIRS and not d.startswith(".git")
                    )
                    for fn in sorted(filenames):
                        scan_roots.append(Path(dirpath) / fn)
                        if len(scan_roots) > 200000:  # pathological tree guard
                            break

            for fp in scan_roots:
                if include and not fnmatch.fnmatch(fp.name, include):
                    continue
                try:
                    if fp.stat().st_size > MAX_GREP_FILE_BYTES:
                        continue
                    data = fp.read_bytes()
                except OSError:
                    continue
                if b"\x00" in data[:8192]:
                    continue  # binary
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                rel = _rel(ctx, fp)
                for lineno, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{rel}:{lineno}:{line}")
                        if len(results) >= MAX_RESULTS:
                            truncated = True
                            break
                if truncated:
                    break

            out = "\n".join(results) if results else "(no matches)"
            return ToolResult.success(out, matches=len(results), truncated=truncated)
        except Exception as e:  # noqa: BLE001
            logger.warning("grep failed", exc_info=True)
            return ToolResult.failure(f"grep failed: {e}")


class WebSearchTool:
    """web_search — DuckDuckGo HTML search."""

    name = "web_search"
    description = (
        "Search the web via DuckDuckGo (HTML endpoint). Returns up to 10 results "
        "with title, URL and snippet. Fails softly when the network is unavailable."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query text.",
            }
        },
        "required": ["query"],
    }
    permission = "network"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", "") or "").strip()
        if not query:
            return ToolResult.failure("query must be a non-empty string")
        try:
            import httpx  # lazy: web tools degrade gracefully without the dep
        except ImportError as e:
            return ToolResult.failure(f"web_search unavailable: {e}")
        try:
            async with httpx.AsyncClient(
                timeout=20, headers=BROWSER_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/", params={"q": query}
                )
                resp.raise_for_status()
                page = resp.text
        except Exception as e:  # noqa: BLE001 — network errors never raise out
            return ToolResult.failure(f"web_search unavailable: {e}")

        try:
            anchors = re.findall(
                r'<a\b([^>]*class="[^"]*result__a[^"]*"[^>]*)>(.*?)</a>',
                page,
                flags=re.DOTALL | re.IGNORECASE,
            )
            snippets = re.findall(
                r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div)>',
                page,
                flags=re.DOTALL | re.IGNORECASE,
            )
            lines: list[str] = []
            for i, (attrs, inner) in enumerate(anchors[:MAX_WEB_RESULTS]):
                hm = re.search(r'href="([^"]+)"', attrs)
                url = _decode_ddg_url(hm.group(1)) if hm else ""
                title = _strip_tags(inner) or "(no title)"
                snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
                block = f"{i + 1}. {title}\n   {url}"
                if snippet:
                    block += f"\n   {snippet}"
                lines.append(block)
            if not lines:
                return ToolResult.success(
                    f"(no results parsed for: {query})", results=0
                )
            return ToolResult.success(
                "\n\n".join(lines), results=len(lines)
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"web_search unavailable: {e}")


class WebFetchTool:
    """web_fetch — fetch a URL and return plain text."""

    name = "web_fetch"
    description = (
        "Fetch an http(s) URL and return its content as plain text (scripts and "
        "styles removed, tags stripped), truncated to max_chars (default 20000)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute http:// or https:// URL to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters of text to return (default 20000).",
            },
        },
        "required": ["url"],
    }
    permission = "network"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "") or "").strip()
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            return ToolResult.failure("only http/https URLs are allowed")
        max_chars = max(1, _to_int(args.get("max_chars"), 20000))
        try:
            import httpx  # lazy: web tools degrade gracefully without the dep
        except ImportError as e:
            return ToolResult.failure(f"web_fetch unavailable: {e}")
        try:
            async with httpx.AsyncClient(
                timeout=20, headers=BROWSER_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                page = resp.text
                final_url = str(resp.url)
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"web_fetch unavailable: {e}")

        try:
            text = _html_to_text(page)
            if len(text) > max_chars:
                text = text[:max_chars] + "\n...[truncated]"
            return ToolResult.success(text, url=final_url, chars=len(text))
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"web_fetch unavailable: {e}")


def all_tools() -> list:
    return [GlobTool(), GrepTool(), WebSearchTool(), WebFetchTool()]

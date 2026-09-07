"""Turn Claude Agent SDK messages into Telegram-ready HTML fragments."""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from typing import Any

SEPARATOR = " \N{MIDDLE DOT} "

# Terminal reasons that just mean "the turn ended normally".
QUIET_TERMINAL_REASONS = frozenset({"end_turn", "success", "completed", "stop_sequence"})

# Telegram's hard limit is 4096 UTF-16 code units; leave room for our wrappers.
CHUNK_LIMIT = 3500

TOOL_ICONS = {
    "Bash": "\N{PERSONAL COMPUTER}",
    "BashOutput": "\N{PERSONAL COMPUTER}",
    "Read": "\N{OPEN BOOK}",
    "Write": "\N{MEMO}",
    "Edit": "\N{LOWER LEFT PENCIL}",
    "NotebookEdit": "\N{LOWER LEFT PENCIL}",
    "Glob": "\N{LEFT-POINTING MAGNIFYING GLASS}",
    "Grep": "\N{LEFT-POINTING MAGNIFYING GLASS}",
    "WebFetch": "\N{GLOBE WITH MERIDIANS}",
    "WebSearch": "\N{GLOBE WITH MERIDIANS}",
    "Task": "\N{ROBOT FACE}",
    "TodoWrite": "\N{CLIPBOARD}",
    "Skill": "\N{ELECTRIC LIGHT BULB}",
}


def esc(text: Any) -> str:
    """HTML-escape a value for Telegram's HTML parse mode."""
    return html.escape(str(text), quote=False)


def truncate(text: str, limit: int, marker: str = "\N{HORIZONTAL ELLIPSIS}") -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + marker


def chunk(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Split plain text into Telegram-sized pieces, preferring line boundaries."""
    text = text.strip("\n")
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        pieces.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining.strip():
        pieces.append(remaining)
    return pieces


def _one_line(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return truncate(text, limit)


def _rel(path: Any, root: str | None) -> str:
    text = str(path)
    if root and text.startswith(root):
        trimmed = text[len(root) :].lstrip("/")
        return trimmed or "."
    return text


def describe_tool_use(name: str, tool_input: dict[str, Any], root: str | None = None) -> str:
    """A one-or-two line HTML summary of a tool call, as it would read in a session log."""
    icon = TOOL_ICONS.get(name, "\N{HAMMER AND WRENCH}")
    head = f"{icon} <b>{esc(name)}</b>"
    detail = _tool_detail(name, tool_input, root)
    return f"{head}\n{detail}" if detail else head


def _tool_detail(name: str, ti: dict[str, Any], root: str | None) -> str:
    if not isinstance(ti, dict):
        return f"<code>{esc(_one_line(ti))}</code>"

    if name in ("Bash", "BashOutput"):
        command = ti.get("command") or ti.get("bash_id") or ""
        lines = [f"<pre>{esc(truncate(str(command), 600))}</pre>"]
        if ti.get("description"):
            lines.insert(0, f"<i>{esc(_one_line(ti['description']))}</i>")
        if ti.get("run_in_background"):
            lines.append("<i>(background)</i>")
        return "\n".join(lines)

    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        path = _rel(ti.get("file_path", ""), root)
        extra = ""
        if name == "Read" and (ti.get("offset") or ti.get("limit")):
            extra = f" <i>(lines {ti.get('offset', 1)}+{ti.get('limit', '')})</i>"
        elif name == "Edit":
            old = _one_line(ti.get("old_string", ""), 80)
            new = _one_line(ti.get("new_string", ""), 80)
            return (
                f"<code>{esc(path)}</code>\n"
                f"<pre>- {esc(old)}\n+ {esc(new)}</pre>"
            )
        elif name == "Write":
            body = str(ti.get("content", ""))
            extra = f" <i>({len(body)} chars)</i>"
        return f"<code>{esc(path)}</code>{extra}"

    if name in ("Glob", "Grep"):
        parts = [f"<code>{esc(ti.get('pattern', ''))}</code>"]
        if ti.get("path"):
            parts.append(f"in <code>{esc(_rel(ti['path'], root))}</code>")
        if ti.get("glob"):
            parts.append(f"glob <code>{esc(ti['glob'])}</code>")
        return " ".join(parts)

    if name in ("WebFetch", "WebSearch"):
        target = ti.get("url") or ti.get("query") or ""
        return f"<code>{esc(_one_line(target))}</code>"

    if name == "Task":
        agent = ti.get("subagent_type", "agent")
        return (
            f"<i>{esc(ti.get('description', ''))}</i> "
            f"\N{RIGHTWARDS ARROW} <code>{esc(agent)}</code>"
        )

    if name == "TodoWrite":
        todos = ti.get("todos") or []
        marks = {"completed": "✅", "in_progress": "▶", "pending": "☐"}
        rows = [
            f"{marks.get(t.get('status'), '☐')} {esc(_one_line(t.get('content', ''), 90))}"
            for t in todos[:12]
        ]
        if len(todos) > 12:
            rows.append(f"\N{HORIZONTAL ELLIPSIS} +{len(todos) - 12} more")
        return "\n".join(rows)

    try:
        blob = json.dumps(ti, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
        blob = str(ti)
    return f"<pre>{esc(truncate(blob, 500))}</pre>"


def tool_result_text(content: Any) -> str:
    """Flatten a ToolResultBlock's content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Iterable):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def render_result_footer(
    *,
    subtype: str,
    is_error: bool,
    duration_ms: int,
    num_turns: int,
    total_cost_usd: float | None,
    terminal_reason: str | None,
) -> str:
    icon = "⚠️" if is_error else "✔️"
    bits = [f"{duration_ms / 1000:.1f}s", f"{num_turns} turn{'s' if num_turns != 1 else ''}"]
    if total_cost_usd:
        bits.append(f"${total_cost_usd:.4f}")
    if terminal_reason and terminal_reason not in QUIET_TERMINAL_REASONS:
        bits.append(esc(terminal_reason))
    elif is_error and subtype:
        bits.append(esc(subtype))
    return f"<i>{icon} {SEPARATOR.join(bits)}</i>"

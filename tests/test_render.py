from __future__ import annotations

from claude_telegram_bot.render import (
    chunk,
    describe_tool_use,
    render_result_footer,
    tool_result_text,
    truncate,
)


def test_chunk_stays_within_telegram_limit():
    pieces = chunk("x" * 20_000)
    assert pieces
    assert all(len(piece) <= 3500 for piece in pieces)
    assert "".join(pieces) == "x" * 20_000


def test_chunk_prefers_line_boundaries():
    body = "\n".join(f"line {i}" for i in range(1200))
    pieces = chunk(body)
    assert len(pieces) > 1
    assert all(not piece.startswith(" ") for piece in pieces)
    assert "".join(p + "\n" for p in pieces).count("line ") == 1200


def test_chunk_empty():
    assert chunk("") == []
    assert chunk("\n\n") == []


def test_bash_tool_is_escaped():
    out = describe_tool_use("Bash", {"command": "echo '<script>' && ls"})
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_paths_are_relative_to_workspace():
    out = describe_tool_use("Read", {"file_path": "/work/src/app.py"}, "/work")
    assert "<code>src/app.py</code>" in out


def test_unknown_tool_falls_back_to_json():
    out = describe_tool_use("MysteryTool", {"a": 1, "b": [2, 3]})
    assert "MysteryTool" in out
    assert "&quot;a&quot;" in out or '"a"' in out


def test_tool_result_text_flattens_blocks():
    assert tool_result_text([{"type": "text", "text": "hi"}, {"type": "image"}]) == "hi\n[image]"
    assert tool_result_text("plain") == "plain"
    assert tool_result_text(None) == ""


def test_truncate_marks_elision():
    assert truncate("abcdef", 3).endswith("\N{HORIZONTAL ELLIPSIS}")
    assert truncate("abc", 10) == "abc"


def test_result_footer_reports_cost_and_turns():
    out = render_result_footer(
        subtype="success",
        is_error=False,
        duration_ms=2500,
        num_turns=1,
        total_cost_usd=0.0042,
        terminal_reason="end_turn",
    )
    assert "2.5s" in out and "1 turn" in out and "$0.0042" in out
    assert "end_turn" not in out


def test_result_footer_flags_errors():
    out = render_result_footer(
        subtype="error_max_turns",
        is_error=True,
        duration_ms=100,
        num_turns=9,
        total_cost_usd=None,
        terminal_reason=None,
    )
    assert "⚠️" in out and "error_max_turns" in out

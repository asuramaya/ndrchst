"""server.properties read/write tests."""
from __future__ import annotations

from pathlib import Path

from ndrchst.domain import properties as p


def test_read_missing_returns_empty(tmp_path: Path):
    assert p.read(tmp_path) == {}


def test_read_parses_kv_skips_comments(tmp_path: Path):
    (tmp_path / "server.properties").write_text(
        "# header\nmotd=Hello\nmax-players=20\n\n# trail\n"
    )
    assert p.read(tmp_path) == {"motd": "Hello", "max-players": "20"}


def test_write_preserves_comments_and_order(tmp_path: Path):
    (tmp_path / "server.properties").write_text(
        "# header comment\n"
        "motd=Old\n"
        "level-name=world\n"
        "max-players=20\n"
    )
    p.write(tmp_path, {"motd": "New", "max-players": "100"})
    text = (tmp_path / "server.properties").read_text()
    assert text.startswith("# header comment\n")
    assert "motd=New" in text
    assert "level-name=world" in text  # untouched
    assert "max-players=100" in text


def test_write_appends_new_keys(tmp_path: Path):
    (tmp_path / "server.properties").write_text("motd=Hi\n")
    p.write(tmp_path, {"motd": "Hi", "online-mode": "false"})
    text = (tmp_path / "server.properties").read_text()
    assert "online-mode=false" in text

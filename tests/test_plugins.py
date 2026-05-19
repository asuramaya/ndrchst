"""Plugin domain tests (Java-only)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from ndrchst.domain import plugins


def _make_jar(*, name="TestPlugin", version="1.0", author="Tester", main="com.example.Main") -> bytes:
    buf = io.BytesIO()
    yml = f"""\
name: {name}
version: {version}
author: {author}
main: {main}
api-version: '1.21'
commands:
  hello:
    description: Say hi
permissions:
  test.use:
    default: true
"""
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.yml", yml)
        zf.writestr("com/example/Main.class", b"\xCA\xFE\xBA\xBEdummy")
    return buf.getvalue()


def test_list_returns_empty_when_plugins_dir_missing(tmp_path: Path):
    assert plugins.list_plugins(tmp_path) == []


def test_list_parses_plugin_yml(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Foo.jar").write_bytes(
        _make_jar(name="Foo", version="2.3.4", author="Alice")
    )
    found = plugins.list_plugins(tmp_path)
    assert len(found) == 1
    p = found[0]
    assert p.filename == "Foo.jar"
    assert p.name == "Foo"
    assert p.version == "2.3.4"
    assert p.author == "Alice"
    assert not p.disabled


def test_list_shows_disabled_jars(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Foo.jar.disabled").write_bytes(_make_jar(name="Foo"))
    found = plugins.list_plugins(tmp_path)
    assert len(found) == 1
    assert found[0].disabled


def test_list_handles_jar_without_plugin_yml(tmp_path: Path):
    """A jar that's not a Bukkit plugin (just a random library) should still
    show up with the filename as a fallback name."""
    (tmp_path / "plugins").mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    (tmp_path / "plugins" / "library.jar").write_bytes(buf.getvalue())
    found = plugins.list_plugins(tmp_path)
    assert len(found) == 1
    assert found[0].filename == "library.jar"
    assert found[0].display_name == "library"


def test_toggle_enabled_to_disabled(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Foo.jar").write_bytes(_make_jar())
    new = plugins.toggle_plugin(tmp_path, "Foo.jar")
    assert new == "Foo.jar.disabled"
    assert (tmp_path / "plugins" / "Foo.jar.disabled").exists()
    assert not (tmp_path / "plugins" / "Foo.jar").exists()


def test_toggle_disabled_to_enabled(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Foo.jar.disabled").write_bytes(_make_jar())
    new = plugins.toggle_plugin(tmp_path, "Foo.jar.disabled")
    assert new == "Foo.jar"


def test_toggle_rejects_path_traversal(tmp_path: Path):
    with pytest.raises(plugins.PluginError):
        plugins.toggle_plugin(tmp_path, "../../etc/passwd")


def test_remove_deletes_file(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    target = tmp_path / "plugins" / "Foo.jar"
    target.write_bytes(_make_jar())
    plugins.remove_plugin(tmp_path, "Foo.jar")
    assert not target.exists()


def test_save_upload_validates_extension(tmp_path: Path):
    with pytest.raises(plugins.PluginError, match=r"\.jar"):
        plugins.save_upload(tmp_path, "evil.exe", io.BytesIO(b"x"))


def test_save_upload_rejects_non_zip(tmp_path: Path):
    with pytest.raises(plugins.PluginError, match="not a valid jar"):
        plugins.save_upload(tmp_path, "broken.jar", io.BytesIO(b"not a zip at all"))


def test_save_upload_rejects_path_separators(tmp_path: Path):
    with pytest.raises(plugins.PluginError, match="unsafe filename"):
        plugins.save_upload(tmp_path, ".sneaky.jar", io.BytesIO(b""))


def test_save_upload_writes_atomically(tmp_path: Path):
    """The .upload temp must not be left behind on success."""
    target = plugins.save_upload(tmp_path, "Foo.jar", io.BytesIO(_make_jar()))
    assert target == tmp_path / "plugins" / "Foo.jar"
    assert target.exists()
    assert not (target.with_suffix(target.suffix + ".upload")).exists()


def test_yaml_scan_inline_authors_list(tmp_path: Path):
    """Some plugins use `authors: [Alice, Bob]` inline form."""
    (tmp_path / "plugins").mkdir()
    buf = io.BytesIO()
    yml = "name: Multi\nversion: 1.0\nauthors: [Alice, Bob]\n"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.yml", yml)
    (tmp_path / "plugins" / "Multi.jar").write_bytes(buf.getvalue())
    found = plugins.list_plugins(tmp_path)
    assert found[0].author == "Alice"

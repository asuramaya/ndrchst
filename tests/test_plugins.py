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


def test_sha1_of_matches_hashlib(tmp_path: Path):
    import hashlib
    target = tmp_path / "blob.jar"
    target.write_bytes(b"some bytes" * 1000)
    assert plugins.sha1_of(target) == hashlib.sha1(b"some bytes" * 1000).hexdigest()


def test_hash_inventory_only_enabled_jars(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "A.jar").write_bytes(_make_jar(name="A"))
    (tmp_path / "plugins" / "B.jar.disabled").write_bytes(_make_jar(name="B"))
    inv = plugins.hash_inventory(tmp_path)
    assert "A.jar" in inv
    assert "B.jar.disabled" not in inv
    assert len(inv["A.jar"]) == 40  # SHA1 hex digest


def test_replace_plugin_swaps_contents(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Old.jar").write_bytes(_make_jar(name="Old", version="1.0"))
    new_bytes = _make_jar(name="Old", version="2.0")
    target = plugins.replace_plugin(tmp_path, "Old.jar", new_bytes)
    assert target.exists()
    # Content updated
    listed = plugins.list_plugins(tmp_path)
    assert listed[0].version == "2.0"


def test_replace_plugin_renames_to_versioned_filename(tmp_path: Path):
    """Modrinth jars often carry the version in the filename. The update path
    must let the new filename replace the old one, removing the legacy file."""
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "Geyser-Spigot.jar").write_bytes(_make_jar(name="Geyser"))
    new_bytes = _make_jar(name="Geyser", version="2.10.1")
    plugins.replace_plugin(
        tmp_path, "Geyser-Spigot.jar", new_bytes,
        new_filename="Geyser-Spigot-2.10.1.jar",
    )
    assert not (tmp_path / "plugins" / "Geyser-Spigot.jar").exists()
    assert (tmp_path / "plugins" / "Geyser-Spigot-2.10.1.jar").exists()


def test_replace_plugin_rejects_bad_zip(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "X.jar").write_bytes(_make_jar(name="X"))
    with pytest.raises(plugins.PluginError, match="not a valid jar"):
        plugins.replace_plugin(tmp_path, "X.jar", b"not a zip")
    # Original still present (atomicity)
    assert (tmp_path / "plugins" / "X.jar").exists()

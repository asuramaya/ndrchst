"""EULA acceptance tests."""
from __future__ import annotations

from pathlib import Path

from ndrchst.domain.models import Family
from ndrchst.runtime.eula import accept_eula


def test_java_writes_eula_txt(tmp_path: Path):
    accept_eula(Family.JAVA, tmp_path)
    eula = tmp_path / "eula.txt"
    assert eula.exists()
    content = eula.read_text()
    assert "eula=true" in content
    assert "ndrchst" in content
    assert "MinecraftEULA" in content


def test_java_preserves_existing_eula(tmp_path: Path):
    custom = "eula=true\n# user notes\n"
    (tmp_path / "eula.txt").write_text(custom)
    accept_eula(Family.JAVA, tmp_path)
    assert (tmp_path / "eula.txt").read_text() == custom


def test_bedrock_no_eula_txt_written(tmp_path: Path):
    accept_eula(Family.BEDROCK, tmp_path)
    # BDS has no eula.txt equivalent
    assert not (tmp_path / "eula.txt").exists()


def test_bedrock_writes_minimum_bookkeeping(tmp_path: Path):
    accept_eula(Family.BEDROCK, tmp_path)
    assert (tmp_path / "permissions.json").read_text() == "[]\n"
    assert (tmp_path / "allowlist.json").read_text() == "[]\n"


def test_bedrock_preserves_existing_files(tmp_path: Path):
    (tmp_path / "permissions.json").write_text('[{"xuid": "1"}]')
    accept_eula(Family.BEDROCK, tmp_path)
    assert (tmp_path / "permissions.json").read_text() == '[{"xuid": "1"}]'

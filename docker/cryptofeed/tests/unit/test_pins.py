"""
Guards against dependency-pin drift between constraints.txt, pyproject.toml and the Dockerfiles.
No server needed.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _pins(path: Path) -> dict:
    """name -> version for the `name==version` lines of a constraints file."""
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)", line.strip())
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def _extra(name: str) -> dict:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    out = {}
    for dep in data["project"]["optional-dependencies"][name]:
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==(.+)$", dep)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def test_cryptofeed_extra_matches_constraints():
    pins = _pins(ROOT / "docker/cryptofeed/constraints.txt")
    for name, version in _extra("cryptofeed").items():
        assert pins.get(name) == version, (
            f"{name}: pyproject {version} vs constraints {pins.get(name)}"
        )


def test_deephaven_extra_matches_constraints():
    pins = _pins(ROOT / "docker/deephaven/constraints.txt")
    for name, version in _extra("deephaven").items():
        assert pins.get(name) == version, (
            f"{name}: pyproject {version} vs constraints {pins.get(name)}"
        )


def test_coinbase_override_targets_the_pinned_cryptofeed():
    """src/coinbase.py is ported to one cryptofeed version; the Dockerfile guard and pin must agree."""
    pinned = _pins(ROOT / "docker/cryptofeed/constraints.txt")["cryptofeed"]
    dockerfile = (ROOT / "docker/cryptofeed/Dockerfile").read_text()
    guard = re.search(r"ARG CRYPTOFEED_HACK_VERSION=(\S+)", dockerfile).group(1)
    assert guard == pinned
    assert f"cryptofeed {pinned}" in (ROOT / "docker/cryptofeed/src/coinbase.py").read_text()


def test_images_use_constraints_and_pinned_bases():
    cf = (ROOT / "docker/cryptofeed/Dockerfile").read_text()
    assert re.search(r"^FROM python:\d+\.\d+\.\d+-", cf, re.M), "pin the Python patch release"
    assert "PIP_CONSTRAINT=/constraints.txt" in cf
    dh = (ROOT / "docker/deephaven/Dockerfile").read_text()
    assert re.search(r"^FROM ghcr\.io/deephaven/server-ui:\d+\.\d+$", dh, re.M)
    assert "-c /tmp/constraints.txt" in dh

import logging
import os
import re
import shutil

import pytest

import salt.version
from tests.conftest import CODE_DIR

log = logging.getLogger(__name__)

PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==\S+$")


def _write_onedir_constraints(shell, script_path, constraints_path):
    """
    Write a pip constraints file pinning every package currently installed in
    the onedir, except salt itself.

    The salt-analytics-framework test extension pulls in dependencies (such as
    pydantic) that can require newer versions of packages than the ones the
    onedir pins. Without constraints, pip resolves that conflict by replacing
    the onedir's salt with a different salt release from PyPI, which breaks
    every test that runs afterwards.
    """
    ret = shell.run(str(script_path), "freeze")
    assert ret.returncode == 0
    pins = []
    for line in ret.stdout.splitlines():
        line = line.strip()
        if not PINNED_REQUIREMENT.match(line):
            continue
        if line.lower().startswith("salt=="):
            continue
        pins.append(line)
    constraints_path.write_text("\n".join(pins) + "\n")


@pytest.fixture(autouse=True)
def _install_salt_extension(shell, tmp_path):
    if os.environ.get("ONEDIR_TESTRUN", "0") == "0":
        yield
        return

    script_name = "salt-pip"
    if salt.utils.platform.is_windows():
        script_name += ".exe"

    script_path = CODE_DIR / "artifacts" / "salt" / script_name
    assert script_path.exists()
    constraints_path = tmp_path / "onedir-constraints.txt"
    try:
        _write_onedir_constraints(shell, script_path, constraints_path)
        ret = shell.run(
            str(script_path),
            "install",
            "-c",
            str(constraints_path),
            "salt-analytics-framework==0.1.0",
        )
        assert ret.returncode == 0
        log.info(ret)
        yield
    finally:
        ret = shell.run(str(script_path), "uninstall", "-y", "salt-analytics-framework")
        log.info(ret)
        shutil.rmtree(script_path.parent / "extras-3.10", ignore_errors=True)


@pytest.mark.windows_whitelisted
def test_versions_report(salt_cli):
    """
    Test that we can re-parse the version report back into
    a similar format with the necessary headers
    """
    expected = salt.version.versions_information()
    # sanitize expected of unnnecessary whitespace
    for _, section in expected.items():
        for key in section:
            if isinstance(section[key], str):
                section[key] = section[key].strip()

    ret = salt_cli.run("--versions-report")
    assert ret.returncode == 0
    assert ret.stdout
    ret_lines = ret.stdout.split("\n")

    assert ret_lines
    # sanitize lines
    ret_lines = [line.strip() for line in ret_lines]

    for header in expected:
        assert f"{header}:" in ret_lines

    ret_dict = {}
    expected_keys = set()
    for line in ret_lines:
        if not line:
            continue
        if line.endswith(":"):
            assert not expected_keys
            current_header = line.rstrip(":")
            assert current_header in expected
            ret_dict[current_header] = {}
            expected_keys = set(expected[current_header].keys())
        else:
            key, *value_list = line.split(":", 1)
            assert value_list
            assert len(value_list) == 1
            value = value_list[0].strip()
            if value == "Not Installed":
                value = None
            ret_dict[current_header][key] = value
            assert key in expected_keys
            expected_keys.remove(key)
    assert not expected_keys

    if os.environ.get("ONEDIR_TESTRUN", "0") == "0":
        assert "pip" in ret_dict["Salt Package Information"]["Package Type"]
        # Stop any more testing
        return

    assert "onedir" in ret_dict["Salt Package Information"]["Package Type"]
    assert "relenv" in ret_dict["Dependency Versions"]
    assert "Salt Extensions" in ret_dict
    assert "salt-analytics-framework" in ret_dict["Salt Extensions"]


def test_help_log(salt_cli):
    """
    Test to ensure when we pass in `--help` the insecure
    log warning is included.
    """
    ret = salt_cli.run("--help")
    count = 0
    # This can be dependent on COLUMNS environment variable
    assert "sensitive data: all, debug, garbage, profile, trace" in " ".join(
        ret.stdout.split()
    )

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from scripts.reading_order_post_v2_qualification import bootstrap_v3


def test_validate_arm_environment_accepts_valid_seed():
    with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "101"}, clear=True):
        bootstrap_v3._validate_arm_environment(["--repeat", "1"])
    with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "202"}, clear=True):
        bootstrap_v3._validate_arm_environment(["--repeat", "2"])
    with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "303"}, clear=True):
        bootstrap_v3._validate_arm_environment(["--repeat", "3"])


def test_validate_arm_environment_rejects_missing_repeat():
    with pytest.raises(RuntimeError, match="missing --repeat"):
        bootstrap_v3._validate_arm_environment([])


def test_validate_arm_environment_rejects_invalid_repeat():
    with pytest.raises(RuntimeError, match="unsupported repeat: 4"):
        bootstrap_v3._validate_arm_environment(["--repeat", "4"])


def test_validate_arm_environment_rejects_missing_seed():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="requires PYTHONHASHSEED=101"):
            bootstrap_v3._validate_arm_environment(["--repeat", "1"])


def test_validate_arm_environment_rejects_wrong_seed():
    with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "999"}, clear=True):
        with pytest.raises(RuntimeError, match="requires PYTHONHASHSEED=101"):
            bootstrap_v3._validate_arm_environment(["--repeat", "1"])


def test_validate_arm_environment_rejects_other_python_vars():
    with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "101", "PYTHONPATH": "/malicious"}, clear=True):
        with pytest.raises(RuntimeError, match="unapproved variable: PYTHONPATH"):
            bootstrap_v3._validate_arm_environment(["--repeat", "1"])


def test_require_isolated_interpreter_arm_mode():
    class FakeFlags:
        isolated = 0
        ignore_environment = 0
        no_site = 1
        no_user_site = 1
        safe_path = 1

    with mock.patch.object(bootstrap_v3.sys, "flags", FakeFlags()):
        with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "101"}, clear=True):
            bootstrap_v3._require_isolated_interpreter("arm", ["--repeat", "1"])

    class FakeFlagsIsolated:
        isolated = 1
        ignore_environment = 1
        no_site = 1
        no_user_site = 1
        safe_path = 1

    with mock.patch.object(bootstrap_v3.sys, "flags", FakeFlagsIsolated()):
        with pytest.raises(RuntimeError, match="must not ignore environment variables"):
            bootstrap_v3._require_isolated_interpreter("arm", ["--repeat", "1"])

    class FakeFlagsNoSafePath:
        isolated = 0
        ignore_environment = 0
        no_site = 1
        no_user_site = 1
        safe_path = 0

    with mock.patch.object(bootstrap_v3.sys, "flags", FakeFlagsNoSafePath()):
        with pytest.raises(RuntimeError, match="requires Python -P"):
            bootstrap_v3._require_isolated_interpreter("arm", ["--repeat", "1"])


def test_interpreter_honors_hash_seed(tmp_path: Path):
    probe_script = tmp_path / "probe.py"
    probe_script.write_text("print(hash('test_string'))", encoding="utf-8")
    
    def run_probe(seed: str) -> str:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-S", "-s", "-P", str(probe_script)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    
    hash_101_a = run_probe("101")
    hash_101_b = run_probe("101")
    assert hash_101_a == hash_101_b
    
    hash_202 = run_probe("202")
    assert hash_101_a != hash_202

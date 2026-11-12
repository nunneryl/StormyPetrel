"""scripts/apt_install.sh: the apt install the full pipeline needs, retried before it fails.

The 2026-11-11 16:28Z full run failed at its first step: the runner could not resolve
azure.archive.ubuntu.com and apt-get install exited 100. The script runs here with stand-ins on
PATH for sudo (runs its arguments), apt-get (logs each call; `install` fails the first
FAKE_APT_FAILS times with FAKE_APT_CODE) and sleep (logs its argument). Every expected value is
written by hand.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pipeline.tests.test_ci_workflow import jobs_of, steps_of, yaml_code

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "apt_install.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "forecast-pipeline.yml"

FAKE_SUDO = '#!/bin/sh\nexec "$@"\n'
FAKE_APT = """#!/bin/sh
echo "$*" >> "$FAKE_DIR/calls"
if [ "$1" = update ]; then exit "${FAKE_UPDATE_CODE:-0}"; fi
n=$(cat "$FAKE_DIR/count" 2>/dev/null || echo 0)
n=$((n + 1)); echo "$n" > "$FAKE_DIR/count"
if [ "$n" -le "${FAKE_APT_FAILS:-0}" ]; then
  echo "E: Temporary failure resolving 'azure.archive.ubuntu.com'" >&2
  exit "${FAKE_APT_CODE:-100}"
fi
exit 0
"""
FAKE_SLEEP = '#!/bin/sh\necho "$1" >> "$FAKE_DIR/sleeps"\n'
UPDATE = "update -qq"
INSTALL = "install -y --no-install-recommends libeccodes0 unzip"


def _run(tmp_path, *pkgs, **env):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in (("sudo", FAKE_SUDO), ("apt-get", FAKE_APT), ("sleep", FAKE_SLEEP)):
        p = bin_ / name
        p.write_text(body)
        p.chmod(0o755)
    e = {k: v for k, v in os.environ.items() if not k.startswith(("APT_", "FAKE_"))}
    e.update({"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(tmp_path)})
    e.update({k: str(v) for k, v in env.items()})
    done = subprocess.run(["bash", str(SCRIPT), *pkgs], env=e, capture_output=True, text=True,
                          timeout=60)

    def lines(name):
        f = tmp_path / name
        return f.read_text().splitlines() if f.exists() else []
    return done, lines("calls"), lines("sleeps")


def test_a_first_time_success_installs_once_and_waits_for_nothing(tmp_path):
    done, calls, sleeps = _run(tmp_path, "libeccodes0", "unzip")
    assert done.returncode == 0
    assert calls == [UPDATE, INSTALL]
    assert sleeps == []
    assert done.stdout == ""


def test_a_transient_failure_is_retried_with_a_short_backoff(tmp_path):
    done, calls, sleeps = _run(tmp_path, "libeccodes0", "unzip", FAKE_APT_FAILS=2)
    assert done.returncode == 0
    assert calls == [UPDATE, INSTALL] * 3
    assert sleeps == ["10", "30"]
    assert done.stdout.splitlines() == [
        "::warning::apt-get install of libeccodes0 unzip failed (exit 100) on attempt 1 of 4; "
        "retrying in 10 s",
        "::warning::apt-get install of libeccodes0 unzip failed (exit 100) on attempt 2 of 4; "
        "retrying in 30 s",
        "apt-get install of libeccodes0 unzip succeeded on attempt 3 of 4"]


def test_success_on_the_last_attempt(tmp_path):
    done, calls, sleeps = _run(tmp_path, "libeccodes0", "unzip", FAKE_APT_FAILS=3)
    assert done.returncode == 0
    assert calls.count(INSTALL) == 4
    assert sleeps == ["10", "30", "60"]
    assert done.stdout.splitlines()[-1] == \
        "apt-get install of libeccodes0 unzip succeeded on attempt 4 of 4"


def test_four_failures_fail_with_apt_gets_own_exit_code_and_one_error_line(tmp_path):
    done, calls, sleeps = _run(tmp_path, "libeccodes0", "unzip", FAKE_APT_FAILS=9)
    assert done.returncode == 100
    assert calls == [UPDATE, INSTALL] * 4
    assert sleeps == ["10", "30", "60"]
    out = done.stdout.splitlines()
    assert out[-1] == "::error::apt-get install of libeccodes0 unzip failed on all 4 attempts (exit 100)"
    assert sum(ln.startswith("::warning::") for ln in out) == 3
    assert sum(ln.startswith("::error::") for ln in out) == 1


def test_another_exit_code_is_passed_through(tmp_path):
    done, _calls, _sleeps = _run(tmp_path, "libeccodes0", "unzip", FAKE_APT_FAILS=9,
                                 FAKE_APT_CODE=7)
    assert done.returncode == 7


def test_a_failed_index_update_does_not_stop_the_install(tmp_path):
    # apt-get update warns and carries on over an index it cannot fetch; only the install counts.
    done, calls, sleeps = _run(tmp_path, "libeccodes0", "unzip", FAKE_UPDATE_CODE=100)
    assert done.returncode == 0
    assert calls == [UPDATE, INSTALL]
    assert sleeps == []


def test_attempts_and_waits_can_be_set_and_the_last_wait_repeats(tmp_path):
    done, calls, sleeps = _run(tmp_path, "unzip", FAKE_APT_FAILS=9, APT_ATTEMPTS=3, APT_WAITS="5")
    assert done.returncode == 100
    assert calls.count("install -y --no-install-recommends unzip") == 3
    assert sleeps == ["5", "5"]


def test_no_packages_is_an_error_and_runs_nothing(tmp_path):
    done, calls, sleeps = _run(tmp_path)
    assert done.returncode == 2
    assert done.stdout == "::error::apt_install.sh: no packages named\n"
    assert calls == [] and sleeps == []


def test_the_full_pipeline_installs_through_the_script():
    code = yaml_code(WORKFLOW.read_text(encoding="utf-8"))
    ss = steps_of(jobs_of(code)["full-pipeline"])
    step = next(keys for keys, _ in ss if keys.get("name") == "Install eccodes (cfgrib's GRIB-2 backend)")
    assert step["run"] == "bash scripts/apt_install.sh libeccodes0 unzip"
    assert "apt-get install" not in code, "every apt install in this workflow goes through the script"

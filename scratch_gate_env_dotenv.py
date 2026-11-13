"""Validates that `codegen.run_command` fills gate subprocesses with real
`.env` values (via `_read_dotenv_values`), not just the mock's own
`env_extra`, so `gate_import` on a *non-entry* sibling file that reads a
credential at module import time sees the same value a real run -- going
through the entry point's injected `.env` loader -- would have given it.

Live failure this reproduces and fixes: a `client.py`-style file doing
`API_KEY = os.environ["API_KEY"]` at module scope failed `gate_import`
with "API_KEY environment variable is not set" even though the swarm's
own `.env` had a real value, because `gate_import` probes the file
standalone and never goes through the entry point's loader.

Offline. No node contacted, no subprocess needed for the parsing half;
the merge behaviour is exercised through `run_command` itself against a
real tiny script, using a monkeypatched `swarm.HERE` pointed at a scratch
`.env`.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen
import swarm


def test_parses_dotenv_values():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# comment\n\nAPI_KEY=real-secret-value\n"
                     "API_BASE_URL=\"https://example.test\"\n"
                     "malformed line with no equals\n")
        values = codegen._read_dotenv_values(path)
        assert values == {"API_KEY": "real-secret-value",
                          "API_BASE_URL": "https://example.test"}, values


def test_missing_dotenv_file_is_silent():
    values = codegen._read_dotenv_values("/nonexistent/path/.env")
    assert values == {}


def _probe_script(tmp):
    path = os.path.join(tmp, "probe.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "import os\n"
            "if 'API_KEY' not in os.environ:\n"
            "    raise OSError('API_KEY environment variable is not set.')\n"
            "print('ok', os.environ['API_KEY'], os.environ.get('API_BASE_URL'))\n"
        )
    return path


def test_gate_import_style_run_sees_real_dotenv_value():
    """The exact live shape: a module that reads a credential at import
    time, gated only via `gate_import`'s `env_extra=api_env`, where
    `api_env` (mirroring a real run) carries nothing but the mock's
    `API_BASE_URL` -- API_KEY has to come from `.env`, not from
    `env_extra`, or this never passes."""
    with tempfile.TemporaryDirectory() as swarm_home, \
        tempfile.TemporaryDirectory() as cwd:
        with open(os.path.join(swarm_home, ".env"), "w", encoding="utf-8") as fh:
            fh.write("API_KEY=real-secret-value\n")
        old_here = swarm.HERE
        swarm.HERE = swarm_home
        old_key = os.environ.pop("API_KEY", None)
        try:
            script = _probe_script(cwd)
            ok, out = codegen.run_command(
                "%s %s" % (sys.executable, script), cwd,
                env_extra={"API_BASE_URL": "http://127.0.0.1:9999"})
            assert ok, "expected the .env value to satisfy the check: %s" % out
            assert "real-secret-value" in out
            assert "http://127.0.0.1:9999" in out
        finally:
            swarm.HERE = old_here
            if old_key is not None:
                os.environ["API_KEY"] = old_key


def test_a_real_export_still_wins_over_dotenv():
    with tempfile.TemporaryDirectory() as swarm_home, \
        tempfile.TemporaryDirectory() as cwd:
        with open(os.path.join(swarm_home, ".env"), "w", encoding="utf-8") as fh:
            fh.write("API_KEY=from-dotenv\n")
        old_here = swarm.HERE
        swarm.HERE = swarm_home
        old_key = os.environ.get("API_KEY")
        os.environ["API_KEY"] = "operator-exported-value"
        try:
            script = _probe_script(cwd)
            ok, out = codegen.run_command(
                "%s %s" % (sys.executable, script), cwd,
                env_extra={"API_BASE_URL": "http://127.0.0.1:9999"})
            assert ok, out
            assert "operator-exported-value" in out
            assert "from-dotenv" not in out
        finally:
            swarm.HERE = old_here
            if old_key is None:
                os.environ.pop("API_KEY", None)
            else:
                os.environ["API_KEY"] = old_key


def test_no_env_extra_means_no_dotenv_injection():
    """Gated the same way `maybe_inject_env_loader` is gated -- a call
    with no `env_extra` at all (no `--api`, no mock) gets no `.env`
    injection either, matching the entry-point loader's own condition."""
    with tempfile.TemporaryDirectory() as swarm_home, \
        tempfile.TemporaryDirectory() as cwd:
        with open(os.path.join(swarm_home, ".env"), "w", encoding="utf-8") as fh:
            fh.write("API_KEY=from-dotenv\n")
        old_here = swarm.HERE
        swarm.HERE = swarm_home
        old_key = os.environ.pop("API_KEY", None)
        try:
            script = _probe_script(cwd)
            ok, out = codegen.run_command(
                "%s %s" % (sys.executable, script), cwd, env_extra=None)
            assert not ok, (
                "with no env_extra at all, .env must not be consulted: %s"
                % out)
        finally:
            swarm.HERE = old_here
            if old_key is not None:
                os.environ["API_KEY"] = old_key


def test_sabotage_without_the_fix_the_live_failure_reproduces():
    """Calls `run_command` bypassing the `.env` merge entirely (as the
    pre-fix code did) to prove this fixture actually exercises the bug:
    with only `env_extra` applied and no `.env` read, the identical probe
    script fails exactly the way the live `client.py` did."""
    with tempfile.TemporaryDirectory() as swarm_home, \
        tempfile.TemporaryDirectory() as cwd:
        with open(os.path.join(swarm_home, ".env"), "w", encoding="utf-8") as fh:
            fh.write("API_KEY=real-secret-value\n")
        old_key = os.environ.pop("API_KEY", None)
        try:
            script = _probe_script(cwd)
            env = dict(os.environ)
            env.update({"API_BASE_URL": "http://127.0.0.1:9999"})
            import subprocess
            proc = subprocess.run([sys.executable, script], cwd=cwd, env=env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
            assert proc.returncode != 0, (
                "sabotage should reproduce the pre-fix failure")
            assert b"API_KEY environment variable is not set" in proc.stdout
        finally:
            if old_key is not None:
                os.environ["API_KEY"] = old_key


def run():
    test_parses_dotenv_values()
    test_missing_dotenv_file_is_silent()
    test_gate_import_style_run_sees_real_dotenv_value()
    test_a_real_export_still_wins_over_dotenv()
    test_no_env_extra_means_no_dotenv_injection()
    test_sabotage_without_the_fix_the_live_failure_reproduces()
    print("scratch_gate_env_dotenv: all checks passed")


if __name__ == "__main__":
    run()

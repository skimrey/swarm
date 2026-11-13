"""Is the credential name the same in every project, or does each invent one?

Three consecutive runs asked for AUTH_TOKEN, API_KEY and API_AUTH_TOKEN.
Every file was internally consistent and every run reported clean; the
operator exported the previous run's name and the script refused to start.

Deriving the name from the files already in the directory was the first
attempt and is the same bug slowed down -- a fresh directory has nothing to
inherit, so each new project invents again. These assertions exist to stop
that fix coming back.
"""
import os
import shutil
import tempfile

import swarm
import team

CLIENT = '''
import os


def main():
    print(os.environ["AUTH_TOKEN"])


if __name__ == "__main__":
    main()
'''


def config_with(names):
    cfg = swarm.Config.load()
    cfg.data = dict(cfg.data)
    if names is None:
        cfg.data.pop("env_names", None)
    else:
        cfg.data["env_names"] = names
    return cfg


def test_the_name_does_not_depend_on_the_directory():
    cfg = config_with(["API_BASE_URL", "API_KEY"])
    empty = tempfile.mkdtemp(prefix="vocab-empty-")
    used = tempfile.mkdtemp(prefix="vocab-used-")
    try:
        with open(os.path.join(used, "client.py"), "w") as fh:
            fh.write(CLIENT)
        assert team.env_vocabulary(cfg, empty) == team.env_vocabulary(cfg, used)
        assert "AUTH_TOKEN" not in team.env_vocabulary(cfg, used), (
            "a name found in the directory leaked into the vocabulary; that "
            "is per-project naming again")
    finally:
        shutil.rmtree(empty, ignore_errors=True)
        shutil.rmtree(used, ignore_errors=True)
    print("  the same names in a fresh directory and a used one")


def test_the_operator_can_change_it_once():
    cfg = config_with(["PLATFORM_URL", "PLATFORM_TOKEN"])
    root = tempfile.mkdtemp(prefix="vocab-")
    try:
        assert team.env_vocabulary(cfg, root) == ["PLATFORM_URL",
                                                  "PLATFORM_TOKEN"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    # A config written before this key existed must still work.
    assert team.env_vocabulary(config_with(None)) == team.DEFAULT_ENV_NAMES
    print("  config.json decides, and an older config still loads")


def test_a_dotenv_adds_its_own_names_without_displacing_the_standard():
    """The two say different things and both have to reach the writers.

    A `.env` is the operator's actual credentials, which is why its names
    are read at all: STRIPE_API_KEY must not come back as STRIPE_KEY. The
    standard pair is what everything else should be called.
    """
    cfg = config_with(["API_BASE_URL", "API_KEY"])
    root = tempfile.mkdtemp(prefix="vocab-")
    try:
        with open(os.path.join(root, ".env"), "w") as fh:
            fh.write("STRIPE_API_KEY=sk_live_do_not_read_this\n")
        names = team.env_vocabulary(cfg, root)
        assert names[:2] == ["API_BASE_URL", "API_KEY"], names
        assert "STRIPE_API_KEY" in names, names
        assert not any("sk_live" in n for n in names), (
            "a value reached the vocabulary: %s" % names)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  a .env contributes names, never values, never a replacement")


def main():
    test_the_name_does_not_depend_on_the_directory()
    test_the_operator_can_change_it_once()
    test_a_dotenv_adds_its_own_names_without_displacing_the_standard()
    print("OK")


if __name__ == "__main__":
    main()

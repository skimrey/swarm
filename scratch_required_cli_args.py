"""Validates `codegen.gate_required_cli_args`.

Live failure this exists to catch: `*mondaysept21-3`'s `05_update_applet`
step had a request that never said where `applet_id`, a version string,
or an artifact URL should come from (no earlier step in that chain ever
created an applet). The writer produced a `main.py` whose argparse parser
declared `applet_id` positional and `--version`/`--artifact_url`
`required=True`. `gate_smoke`'s own placeholder-filling filled all three,
so the file passed every gate and shipped clean. Every real invocation
inside `orchestrate.sh` (`python3 main.py`, always zero arguments) then
failed identically, and `team do --retry --run` reported it FAILED three
times running -- reruns of the identical zero-argument call against
identical code, because nothing about the code was ever wrong; there was
simply no CLI argument coming.
"""
import ast
import sys

import codegen


REQUIRED_POSITIONAL = '''
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("applet_id")
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact_url", required=True)
    args = parser.parse_args()
    print(args.applet_id, args.version, args.artifact_url)

if __name__ == "__main__":
    main()
'''

OPTIONAL_NOT_REQUIRED = '''
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--limit", default=10, type=int)
    args = parser.parse_args()
    print(args.verbose, args.limit)

if __name__ == "__main__":
    main()
'''

POSITIONAL_WITH_NARGS_OPTIONAL = '''
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("applet_id", nargs="?", default=None)
    args = parser.parse_args()
    print(args.applet_id)

if __name__ == "__main__":
    main()
'''

POSITIONAL_WITH_DEFAULT = '''
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("applet_id", nargs="*", default=[])
    args = parser.parse_args()
    print(args.applet_id)

if __name__ == "__main__":
    main()
'''

NO_ARGPARSE_AT_ALL = '''
import os

def main():
    applet_id = os.environ["APPLET_ID"]
    print(applet_id)

if __name__ == "__main__":
    main()
'''

READS_FROM_ENV_CORRECTLY = '''
import argparse
import os
import json

def main():
    with open("../05_create_applet/output.json") as fh:
        applet_id = json.load(fh)["applet_id"]
    version = os.environ.get("VERSION", "v1")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(applet_id, version, args.dry_run)

if __name__ == "__main__":
    main()
'''

FROM_IMPORT_ARGPARSE = '''
from argparse import ArgumentParser

def main():
    parser = ArgumentParser()
    parser.add_argument("task_id")
    args = parser.parse_args()
    print(args.task_id)

if __name__ == "__main__":
    main()
'''


def check(cond, msg):
    if not cond:
        print("FAIL: " + msg)
        sys.exit(1)
    print("ok: " + msg)


def main():
    for src in (REQUIRED_POSITIONAL, OPTIONAL_NOT_REQUIRED,
                POSITIONAL_WITH_NARGS_OPTIONAL, POSITIONAL_WITH_DEFAULT,
                NO_ARGPARSE_AT_ALL, READS_FROM_ENV_CORRECTLY,
                FROM_IMPORT_ARGPARSE):
        ast.parse(src)  # fixtures themselves must be valid python

    # The exact live shape: a positional plus two required=True flags.
    result = codegen.gate_required_cli_args(REQUIRED_POSITIONAL)
    check(result is not None, "flags the live 05_update_applet shape")
    check("applet_id" in result and "--version" in result
          and "--artifact_url" in result,
          "names every required argument in the message")

    check(codegen.gate_required_cli_args(OPTIONAL_NOT_REQUIRED) is None,
          "does not flag ordinary optional flags with defaults")

    check(codegen.gate_required_cli_args(POSITIONAL_WITH_NARGS_OPTIONAL)
          is None,
          "does not flag a positional made optional via nargs='?'")

    check(codegen.gate_required_cli_args(POSITIONAL_WITH_DEFAULT) is None,
          "does not flag a positional made optional via nargs='*' + default")

    check(codegen.gate_required_cli_args(NO_ARGPARSE_AT_ALL) is None,
          "does not flag a file with no argparse at all "
          "(reads from os.environ instead)")

    check(codegen.gate_required_cli_args(READS_FROM_ENV_CORRECTLY) is None,
          "does not flag a file that reads the real values from env/"
          "sibling output.json and only uses argparse for a genuinely "
          "optional flag")

    check(codegen.gate_required_cli_args(FROM_IMPORT_ARGPARSE) is not None,
          "also catches `from argparse import ArgumentParser` "
          "(not just `argparse.ArgumentParser`)")

    # `generate_file`'s new parameter defaults to False, so nothing about
    # ordinary, non-chain generation changes just because this gate now
    # exists.
    import inspect
    sig = inspect.signature(codegen.generate_file)
    check(sig.parameters["forbid_required_cli_args"].default is False,
          "generate_file's new switch defaults to off")

    # Sabotage: confirm the gate is actually what stands between the live
    # shape and a clean pass -- calling the *wrong* gate (any of the other
    # python-only gates) on the exact same fixture finds nothing, proving
    # this is a genuinely new check, not a duplicate of an existing one.
    check(codegen.gate_output_json_key("", REQUIRED_POSITIONAL) is None,
          "sabotage: an existing, unrelated gate does not happen to "
          "catch this by accident")

    print("\nall gate_required_cli_args checks passed")


if __name__ == "__main__":
    main()

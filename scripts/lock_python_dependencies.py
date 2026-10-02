"""Regenerate the supported Windows CPython 3.12 AMD64 dependency locks.

Run in an isolated maintainer environment with pip==26.2.1 and pip-tools==7.6.1.
Application users install the checked-in locks and do not need pip-tools.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_VERSIONS = {"pip": "26.2.1", "pip-tools": "7.6.1"}


def validate_generator() -> None:
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12)
            or sys.platform != "win32" or sysconfig.get_platform() != "win-amd64"):
        raise RuntimeError("Generate these locks on Windows CPython 3.12 AMD64")
    for name, version in GENERATOR_VERSIONS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"Install {name}=={version} in the generator environment") from exc
        if actual != version:
            raise RuntimeError(f"Expected {name}=={version}; found {actual}")


def generator_environment(root: Path, *, upgrade: bool) -> dict[str, str]:
    # Reject accidental extra indexes, local paths and credentials inherited from
    # a developer's pip configuration. The committed inputs use official PyPI.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PIP_") and key != "CUSTOM_COMPILE_COMMAND"}
    env.update({
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_CACHE_DIR": str(root / "output" / "python-lock-cache" / "pip"),
        "PIP_TOOLS_CACHE_DIR": str(root / "output" / "python-lock-cache" / "compile"),
        "PYTHONUTF8": "1",
        "CUSTOM_COMPILE_COMMAND": "python scripts/lock_python_dependencies.py" + (" --upgrade" if upgrade else ""),
    })
    return env


def compile_commands(*, upgrade: bool) -> list[list[str]]:
    common = [
        "--generate-hashes", "--allow-unsafe", "--strip-extras", "--resolver=backtracking",
        "--no-config", "--newline=lf", "--index-url=https://pypi.org/simple",
        "--no-emit-index-url", "--no-emit-trusted-host", "--pip-args=--only-binary=:all:", "--quiet",
    ]
    if upgrade:
        common.append("--upgrade")
    commands = []
    for scope in ("runtime", "test"):
        command = [sys.executable, "-m", "piptools", "compile", f"requirements-{scope}.txt",
                   f"--output-file=requirements/locks/{scope}-windows-py312.txt", *common]
        if scope == "test":
            command.append("--constraint=requirements/locks/runtime-windows-py312.txt")
        commands.append(command)
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade", action="store_true", help="Explicitly re-resolve transitive dependencies")
    args = parser.parse_args()
    try:
        validate_generator()
        for command in compile_commands(upgrade=args.upgrade):
            subprocess.run(command, cwd=ROOT, env=generator_environment(ROOT, upgrade=args.upgrade), check=True)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Dependency lock generation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

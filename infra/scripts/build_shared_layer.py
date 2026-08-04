#!/usr/bin/env python3
"""Builds the `python/` directory that `data.archive_file.shared_layer` zips.

Invoked by `data.external.shared_layer_build` in infra/layers.tf, which means
Terraform runs this during `plan` (external data sources are read every plan,
same as the old pure-`archive_file` approach) and feeds it a JSON query on
stdin, expecting exactly one JSON object back on stdout — so all subprocess
and progress output below is redirected to stderr.

Third-party deps are installed as prebuilt wheels for the *target* Lambda
architecture via `uv pip install --python-platform/--only-binary`, not a plain
install for the host machine. That matters because pydantic ships a compiled
extension (pydantic-core): a wheel built for the dev machine's arch would
silently fail to import on a Lambda running the other architecture. No
compiler or Docker/QEMU is needed since uv only downloads and unpacks a
matching wheel, it doesn't build or execute one. Uses `uv` rather than `pip`
because this repo's tooling already requires it (Makefile, CI) and a uv-managed
venv has no guarantee `pip` itself is installed into it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Lambda's Amazon Linux 2023 runtime is glibc-based and satisfies the older
# manylinux2014 tag, which is what pydantic-core (and most compiled PyPI
# packages) publish wheels under. Keyed on the Lambda architecture name
# (var.lambda_architecture); uv's --python-platform values use the Rust target
# triple's "aarch64", not AWS's "arm64".
PYTHON_PLATFORMS = {
    "arm64": "aarch64-manylinux2014",
    "x86_64": "x86_64-manylinux2014",
}


def main() -> None:
    query = json.load(sys.stdin)
    shared_dir = Path(query["shared_dir"])
    requirements = Path(query["requirements"])
    output_dir = Path(query["output_dir"])
    architecture = query["architecture"]
    python_version = query["python_version"]

    python_dir = output_dir / "python"
    marker = output_dir / ".requirements.sha256"
    requirements_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()

    # Skip the pip install (slow: dependency resolution + download) when
    # requirements.txt hasn't changed since the last build. Safe because the
    # install is fully determined by that file's pinned versions.
    if not marker.exists() or marker.read_text().strip() != requirements_hash:
        if python_dir.exists():
            shutil.rmtree(python_dir)
        python_dir.mkdir(parents=True)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--only-binary",
                ":all:",
                "--python-platform",
                PYTHON_PLATFORMS[architecture],
                "--python-version",
                python_version,
                "--target",
                str(python_dir),
                "-r",
                str(requirements),
            ],
            check=True,
            stdout=sys.stderr,
        )
        marker.write_text(requirements_hash)

    # First-party code changes far more often than dependencies and is cheap
    # to refresh unconditionally on every plan.
    shared_target = python_dir / "shared"
    if shared_target.exists():
        shutil.rmtree(shared_target)
    for py_file in shared_dir.rglob("*.py"):
        dest = shared_target / py_file.relative_to(shared_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(py_file, dest)

    json.dump({"output_dir": str(output_dir)}, sys.stdout)


if __name__ == "__main__":
    main()

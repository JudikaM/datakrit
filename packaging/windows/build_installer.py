"""Build the installer of the Datakrit Windows app with Inno Setup 6.

Run from the repository root after ``build_app.py`` (which made ``dist-exe/Datakrit/``):

    python packaging/windows/build_installer.py [--dist dist-exe] [--iscc PATH]

It compiles ``packaging/windows/datakrit.iss`` into
``<dist>/Datakrit-<version>-windows-x64-setup.exe`` and writes its checksum file
``<setup>.sha256`` (one line ``<hash>  <name>`` ending in LF). The Inno Setup compiler is
``--iscc``, else the ``ISCC`` environment variable, else ``ISCC.exe`` on the PATH or in the
usual Inno Setup 6 folders. It prints the version, the installer's name, size, and SHA-256
as ``key=value`` lines (``setup``, ``setup_bytes``, ``setup_sha256``), and also appends them
to the file named by ``GITHUB_OUTPUT`` when GitHub Actions sets it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from build_app import ROOT, sha256, sha256_line, source_version

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "datakrit.iss"
SETUP = "Datakrit-{version}-windows-x64-setup.exe"
ISCC_FOLDERS = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")


def find_iscc(given: str | None, env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    candidates = [given, env.get("ISCC"), shutil.which("ISCC.exe")]
    for variable in ISCC_FOLDERS:
        if env.get(variable):
            candidates.append(str(Path(env[variable]) / "Inno Setup 6" / "ISCC.exe"))
            candidates.append(str(Path(env[variable]) / "Programs" / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit(
        "the Inno Setup 6 compiler (ISCC.exe) was not found; install Inno Setup 6 and pass "
        "--iscc PATH or set ISCC"
    )


def iscc_command(iscc: Path, version: str, source: Path, output: Path) -> list[str]:
    return [
        str(iscc),
        "/Qp",
        f"/DAppVersion={version}",
        f"/DSourceDir={source}",
        f"/DOutputDir={output}",
        str(SCRIPT),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Datakrit installer.")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist-exe")
    parser.add_argument("--iscc", help="path of ISCC.exe")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("the installer is built on Windows")
    version = source_version()
    dist = args.dist.resolve()
    source = dist / "Datakrit"
    if not (source / "Datakrit.exe").is_file() or not (source / "licenses").is_dir():
        raise SystemExit(f"{source} has no app build with licenses; run build_app.py first")
    target = dist / SETUP.format(version=version)
    target.unlink(missing_ok=True)
    subprocess.run(iscc_command(find_iscc(args.iscc), version, source, dist), check=True)
    if not target.is_file():
        raise SystemExit(f"Inno Setup did not write {target}")
    digest = sha256(target)
    Path(f"{target}.sha256").write_bytes(sha256_line(target, digest))
    result = {
        "version": version,
        "setup": target.name,
        "setup_bytes": str(target.stat().st_size),
        "setup_sha256": digest,
    }
    lines = [f"{key}={value}" for key, value in result.items()]
    print("\n".join(lines))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("\n".join(lines[1:]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

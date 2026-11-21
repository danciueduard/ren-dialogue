"""Build the Windows bridge executable and its distribution archive."""

from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent
PORTABLE_FOLDER = "RenDialogue"
EXECUTABLE_NAME = "Ren Dialogue"
RUNTIME_PACKAGES = (
    "fastapi", "starlette", "pydantic", "pydantic-core", "httpx", "httpcore", "uvicorn",
    "h11", "anyio", "idna", "certifi", "click", "typing-extensions", "typing-inspection",
    "annotated-types", "annotated-doc", "opentelemetry-api", "PyInstaller", "setuptools",
)


def notices():
    parts = []
    license_path = Path(sys.base_prefix) / "LICENSE.txt"
    if license_path.is_file():
        parts.append("Python\n" + license_path.read_text(encoding="utf-8", errors="replace"))
    for package in RUNTIME_PACKAGES:
        try:
            distribution = metadata.distribution(package)
        except metadata.PackageNotFoundError:
            continue
        parts.append(f"{package} {distribution.version}")
        for entry in distribution.files or ():
            if ".dist-info" in str(entry) and entry.name.lower().startswith(("license", "notice", "copying")):
                parts.append(distribution.locate_file(entry).read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(parts)


def build():
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    package = release / f"{PORTABLE_FOLDER}-Windows-x64.zip"
    with TemporaryDirectory() as folder:
        temp = Path(folder)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile",
            "--name", EXECUTABLE_NAME, "--distpath", str(temp / "dist"),
            "--workpath", str(temp / "work"), "--specpath", str(temp),
            "--paths", str(ROOT), "--collect-submodules", "uvicorn",
            str(ROOT / "desktop.py"),
        ], cwd=ROOT, check=True)
        executable = release / f"{EXECUTABLE_NAME}.exe"
        shutil.copy2(temp / "dist" / executable.name, executable)
        guide = release / "START HERE.txt"
        shutil.copy2(ROOT / guide.name, guide)
        legal = release / "THIRD-PARTY-NOTICES.txt"
        legal.write_text(notices(), encoding="utf-8")
        with ZipFile(package, "w", ZIP_DEFLATED) as bundle:
            bundle.mkdir(PORTABLE_FOLDER)
            bundle.mkdir(f"{PORTABLE_FOLDER}/data")
            bundle.mkdir(f"{PORTABLE_FOLDER}/ai model")
            for item in (executable, guide, legal):
                bundle.write(item, f"{PORTABLE_FOLDER}/{item.name}")
    print(package)


if __name__ == "__main__":
    build()

"""Collect actual wheel notices, license texts and corresponding upstream sources.

Only this build helper accesses the network. Cached downloads are reused. The
source packaging step includes matching archives in the application and release.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

import pymupdf
from release_common import ocr_packages

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "LICENSES"
CACHE = ROOT / ".tools" / "sources"
DEST.mkdir(exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)


def fetch(url: str, target: Path, sha256: str | None = None) -> Path:
    if not target.exists():
        print(f"Downloading {target.name}", flush=True)
        temporary = target.with_suffix(target.suffix + ".partial")
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as out:
            shutil.copyfileobj(response, out)
        temporary.replace(target)
    if sha256 and hashlib.file_digest(target.open("rb"), "sha256").hexdigest() != sha256:
        raise RuntimeError(f"Checksum mismatch: {target.name}")
    return target


def main() -> None:
    if sys.platform == "win32":
        native = DEST / "NativeShell"
        native.mkdir(exist_ok=True)
        toolchain = ROOT / ".tools" / "llvm-mingw-20260826-ucrt-x86_64"
        runtime = toolchain / "x86_64-w64-mingw32" / "share" / "mingw32"
        for source in (toolchain / "LICENSE.TXT", runtime / "COPYING.MinGW-w64-runtime.txt", runtime / "COPYING.MinGW-w64.txt", runtime / "COPYING.winpthreads.txt"):
            if not source.is_file():
                raise RuntimeError("Run scripts/build-shell.ps1 before collecting native runtime notices")
            shutil.copyfile(source, native / source.name)
    packages = ["PyMuPDF", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "Pillow", "fonttools", "PyInstaller"]
    packages += [name for name in ocr_packages(ROOT) if name.lower() not in {entry.lower() for entry in packages}]
    installed = {}
    for name in packages:
        dist = importlib.metadata.distribution(name)
        installed[name] = dist.version
        target_dir = DEST / name
        target_dir.mkdir(exist_ok=True)
        (target_dir / "METADATA.txt").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        for member in dist.files or []:
            normalized = str(member).replace("\\", "/")
            base = Path(normalized).name.lower()
            if ("/licenses/" in normalized or base.startswith(("copying", "license", "notice", "thirdpartynotices"))) and '..' not in Path(normalized).parts:
                relative = normalized.split(".dist-info/", 1)[1] if ".dist-info/" in normalized else normalized
                output = target_dir / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dist.locate_file(member), output)

    upstream = "https://raw.githubusercontent.com"
    texts = {
        "AGPL-3.0.txt": f"{upstream}/ArtifexSoftware/mupdf/1.28.2/COPYING",
        "LGPL-3.0.txt": f"{upstream}/qt/qtbase/v6.11.2/LICENSES/LGPL-3.0-only.txt",
        "GPL-3.0.txt": f"{upstream}/qt/qtbase/v6.11.2/LICENSES/GPL-3.0-only.txt",
        "Python-LICENSE.txt": f"{upstream}/python/cpython/v{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}/LICENSE",
        "Inno-Setup-LICENSE.txt": f"{upstream}/jrsoftware/issrc/is-6_7_3/license.txt",
        "PaddleOCR-APACHE-2.0.txt": f"{upstream}/PaddlePaddle/PaddleOCR/v3.3.3/LICENSE",
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda item: fetch(item[1], DEST / item[0]), texts.items()))

    sources = []
    git_sources = {
        'flatbuffers': ('google/flatbuffers', '282dcb1c3266b45600510da4810092f6ec4c85f2'),
        'onnxruntime': ('microsoft/onnxruntime', '2e2543fbe9fae542f921d47a72d21d5a4ef0b710'),
        'rapid-layout': ('RapidAI/RapidLayout', '4cfe33f6ceb3deb7c3a0eb242fd8db57994a7ac4'),
        'rapid-table': ('RapidAI/RapidTable', '22592283c1f9d7c5a014c96c4ecc57de0b3ebfce'),
        'rapidocr': ('RapidAI/RapidOCR', '095232a4c94f7f0e6600ba5bba1177010ad696d4'),
    }
    for package in dict.fromkeys(['pymupdf', 'pillow', 'fonttools', *ocr_packages(ROOT)]):
        version = importlib.metadata.version(package)
        if package in git_sources:
            repo, commit = git_sources[package]
            sources.append(dict(name=package, version=version, commit=commit,
                url=f'https://codeload.github.com/{repo}/tar.gz/{commit}',
                filename=f'{package}-{version}-source.tar.gz', sha256=None))
            continue
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as response:
            metadata = json.load(response)
        source = next(entry for entry in metadata["urls"] if entry["packagetype"] == "sdist")
        sources.append({"name": package, "version": version, "url": source["url"], "filename": source["filename"], "sha256": source["digests"]["sha256"]})
    qt_version = installed["PySide6"]
    qt_base = f"https://download.qt.io/official_releases/qt/{'.'.join(qt_version.split('.')[:2])}/{qt_version}/submodules"
    qt_sources = {name: f"{qt_base}/{name}-everywhere-src-{qt_version}.tar.xz" for name in ("qtbase", "qtsvg", "qtimageformats")}
    qt_sources["pyside-setup"] = f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{qt_version}-src/pyside-setup-everywhere-src-{qt_version}.tar.xz"
    for name, url in qt_sources.items():
        with urllib.request.urlopen(url + ".sha256", timeout=30) as response:
            sha = response.read().decode("utf-8").split()[0]
        sources.append({"name": name, "version": qt_version, "url": url, "filename": url.rsplit("/", 1)[1], "sha256": sha})

    mupdf_version = pymupdf.version[1]
    sources.append({"name": "mupdf", "version": mupdf_version,
                    "url": f"https://mupdf.com/downloads/archive/mupdf-{mupdf_version}-source.tar.gz",
                    "filename": f"mupdf-{mupdf_version}-source.tar.gz", "sha256": None})
    import shapely
    geos_version = shapely.geos_version_string
    sources.append(dict(name='geos', version=geos_version,
        url=f'https://download.osgeo.org/geos/geos-{geos_version}.tar.bz2',
        filename=f'geos-{geos_version}.tar.bz2', sha256=None))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda entry: fetch(entry["url"], CACHE / entry["filename"], entry["sha256"]), sources))
    for source in sources:
        if source["sha256"] is None:
            with (CACHE / source["filename"]).open("rb") as data:
                source["sha256"] = hashlib.file_digest(data, "sha256").hexdigest()
            source["checksum_origin"] = "Computed from the archive retrieved over HTTPS from the upstream project."
    # Flatten notice paths: upstream Qt files can otherwise exceed MAX_PATH
    # when installed beneath a long Windows profile or selected install folder.
    upstream_output = DEST / "upstream"
    if upstream_output.exists():
        resolved = upstream_output.resolve()
        if not resolved.is_relative_to(ROOT.resolve()) or resolved != (ROOT.resolve() / "LICENSES" / "upstream"):
            raise RuntimeError("Generated notice cleanup escaped the project directory")
        for directory, folders, files in os.walk(upstream_output):
            for path in [Path(directory), *(Path(directory)/name for name in folders+files)]:
                if path.is_symlink() or path.is_junction():
                    raise RuntimeError('Generated notices contain a link: '+str(path))
        shutil.rmtree(upstream_output)
    # Retain copyright and license files without extracting arbitrary paths.
    def notices(archive: tarfile.TarFile, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        index = {}
        for member in archive.getmembers():
            path = Path(member.name)
            low = path.name.lower()
            if not member.isfile() or ".." in path.parts or path.is_absolute():
                continue
            if (low.startswith(("copying", "license", "copyright", "notice")) or low == "qt_attribution.json" or low == "qt_attributions.json") and member.size < 1_000_000:
                name = hashlib.sha256(member.name.encode()).hexdigest()[:12] + "-" + path.name[:100] + ".txt"
                out = destination / name
                index[name] = member.name
                with archive.extractfile(member) as data:
                    out.write_bytes(data.read())
            elif low.startswith("mupdf") and low.endswith((".tar.gz", ".tgz")):
                with archive.extractfile(member) as data, tarfile.open(fileobj=data, mode="r:gz") as nested:
                    notices(nested, destination / "bundled-mupdf")
        (destination / "INDEX.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for source in sources:
        print(f"Collecting notices from {source['name']}", flush=True)
        with tarfile.open(CACHE / source["filename"], "r:*") as archive:
            notices(archive, DEST / "upstream" / source["name"])
    manifest = {"application": "ADF", "version": "0.3.24", "python": sys.version,
                "packages": installed, "source_archives": sources,
                "license_text_sources": texts, "library_modifications": "None; official upstream wheels are bundled."}
    sys.path.insert(0, str(ROOT))
    from adf.ocr_models import MODELS
    manifest['ocr_models'] = MODELS
    manifest['ocr'] = dict(runtime='ONNX Runtime CPU', network='Offline; model files are included in the installer.',
        model_origin='PaddlePaddle / PaddleOCR; ONNX conversions distributed by RapidAI',
        model_license='Apache-2.0', notices='PaddleOCR-APACHE-2.0.txt',
        upstream_models=['https://huggingface.co/PaddlePaddle/PP-DocLayoutV3',
                         'https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec'])
    if sys.platform == "win32":
        manifest["native_shell"] = {
            "version": "0.3.24", "source": "native-shell/",
            "toolchain": "LLVM-MinGW 20260826 (LLVM 23.1.0)",
            "toolchain_source": "https://github.com/mstorsjo/llvm-mingw/tree/20260826",
            "llvm_source": "https://github.com/llvm/llvm-project/tree/llvmorg-23.1.0",
            "mingw_w64_source": "https://github.com/mingw-w64/mingw-w64/tree/a3d708261d5ba659205067cb82cae36e7ae8bbb0",
            "linkage": "Static C++ runtime; Windows system DLLs only",
            "runtime_notices": "NativeShell/",
        }
    (DEST / "build-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Licenses and matching source archives ready.", flush=True)


if __name__ == "__main__":
    main()

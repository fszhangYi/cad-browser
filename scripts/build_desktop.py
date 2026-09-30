#!/usr/bin/env python3
"""Build cad-browser self-contained packages (native Linux or Windows via Wine).

Aligned with office_skills:
  - scheme-a-linux-to-linux-desktop
  - scheme-a-linux-to-windows-desktop

  ./build.sh                     → Linux onedir under release/
  ./build.sh --target=windows    → Windows onedir via Wine + embed CPython
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "cad-browser"
APP_EXE = "cad-browser"
PIP_INDEX = os.environ.get("PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _run(cmd: list[str], *, env: dict | None = None, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, env=env, cwd=str(cwd or ROOT))


def _linux_to_wine_path(path: Path) -> str:
    p = path.resolve()
    return "Z:" + str(p).replace("/", "\\")


def _platform_tag() -> str:
    machine = os.uname().machine.lower()
    if machine in {"x86_64", "amd64"}:
        return "linux-x64"
    if machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    return f"linux-{machine}"


def _ensure_linux_venv(tools: Path) -> Path:
    venv = tools / "linux-venv"
    py = venv / "bin" / "python"
    if py.is_file():
        return venv
    tools.mkdir(parents=True, exist_ok=True)
    creators: list[str] = []
    for cand in ("python3.10", "python3.11", sys.executable, "python3"):
        path = cand if "/" in cand else shutil.which(cand)
        if path and path not in creators:
            creators.append(path)
    if not creators:
        raise SystemExit("python3 not found for Linux venv")
    last_err: Exception | None = None
    for creator in creators:
        try:
            if venv.exists():
                shutil.rmtree(venv)
            _run([creator, "-m", "venv", str(venv)])
            if (venv / "bin" / "python").is_file():
                print(f"[linux-venv] created with {creator}", flush=True)
                return venv
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[linux-venv] skip {creator}: {exc}", flush=True)
    raise SystemExit(f"failed to create linux venv: {last_err}")


def _ensure_win_python(tools: Path) -> Path:
    py = tools / "win-python"
    python_exe = py / "python.exe"
    if python_exe.is_file():
        return py

    tools.mkdir(parents=True, exist_ok=True)
    ver = "3.10.11"
    urls = [
        f"https://registry.npmmirror.com/-/binary/python/{ver}/python-{ver}-embed-amd64.zip",
        f"https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip",
    ]
    zip_path = tools / f"python-{ver}-embed-amd64.zip"
    if not zip_path.is_file() or zip_path.stat().st_size < 1_000_000:
        ok = False
        for url in urls:
            try:
                _run(["curl", "-L", "--retry", "3", "-o", str(zip_path), url])
                if zip_path.is_file() and zip_path.stat().st_size > 1_000_000:
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            raise SystemExit("failed to download embeddable CPython")
    if py.exists():
        shutil.rmtree(py)
    py.mkdir(parents=True)
    _run(["unzip", "-o", str(zip_path), "-d", str(py)])

    pth = next(py.glob("python*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("#") and "import site" in s:
            out.append("import site")
        elif s == "import site":
            out.append("import site")
        else:
            out.append(line)
    if "import site" not in "\n".join(out):
        out.append("import site")
    if "Lib\\site-packages" not in "\n".join(out) and "Lib/site-packages" not in "\n".join(out):
        out.append("Lib\\site-packages")
    pth.write_text("\n".join(out) + "\n", encoding="utf-8")
    (py / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)

    get_pip = tools / "get-pip.py"
    if not get_pip.is_file():
        _run(["curl", "-L", "-o", str(get_pip), "https://bootstrap.pypa.io/get-pip.py"])
    return py


def _wine_env(prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WINEPREFIX"] = str(prefix)
    env["WINEARCH"] = "win64"
    env["WINEDEBUG"] = "-all"
    env["PIP_INDEX_URL"] = PIP_INDEX
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _wine_bin() -> str:
    for cand in ("wine64", "wine"):
        if shutil.which(cand):
            return cand
    raise SystemExit("wine64/wine not found — install wine (win64) first")


def _postprocess_windows(built: Path, py_dir: Path) -> None:
    internal = built / "_internal"
    internal.mkdir(parents=True, exist_ok=True)
    embed_zip = py_dir / "python310.zip"
    if embed_zip.is_file():
        shutil.copy2(embed_zip, internal / "python310.zip")

    base = internal / "base_library.zip"
    if not base.is_file():
        hits = list(internal.glob("base_library.zip"))
        if hits:
            base = hits[0]
    if not base.is_file() or not embed_zip.is_file():
        print("[warn] skip base_library inject (missing zip)", flush=True)
        return

    need_prefixes = (
        "pkgutil",
        "inspect",
        "copy",
        "pathlib",
        "typing",
        "contextlib",
        "dataclasses",
        "importlib",
        "json",
        "logging",
        "collections",
        "urllib",
        "email",
        "html",
        "xml",
        "encodings",
        "asyncio",
        "concurrent",
        "multiprocessing",
        "zoneinfo",
        "http",
        "socketserver",
        "argparse",
        "zipfile",
    )
    with zipfile.ZipFile(embed_zip, "r") as src, zipfile.ZipFile(base, "a") as dst:
        existing = set(dst.namelist())
        for name in src.namelist():
            top = name.split("/")[0].split(".")[0]
            if any(
                top == p or name.startswith(p + "/") or name.startswith(p + ".")
                for p in need_prefixes
            ):
                if name not in existing:
                    dst.writestr(name, src.read(name))


def _write_release_meta(
    release: Path,
    *,
    platform: str,
    entry: str,
    notes: str,
    readme_lines: list[str],
) -> None:
    stamp = release.name.rsplit("-", 1)[-1] if "-" in release.name else _utc_stamp()
    # Prefer stamp already encoded in folder name
    parts = release.name.split("-")
    stamp = parts[-1] if parts else _utc_stamp()
    info = {
        "product": APP_ID,
        "app_id": APP_ID,
        "kind": "server",
        "platform": platform,
        "self_contained": True,
        "requires_python_on_target": False,
        "built_at": stamp,
        "built_at_utc": stamp,
        "entry": entry,
        "notes": notes,
    }
    (release / "BUILD_INFO.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (release / "README.txt").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")


def _zip_release(release: Path) -> Path:
    zip_path = Path(str(release) + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in release.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(release.parent)))
    return zip_path


def build_native() -> Path:
    tools = ROOT / ".tools"
    venv = _ensure_linux_venv(tools)
    py = str(venv / "bin" / "python")
    pip = [py, "-m", "pip"]

    _run([*pip, "install", "-U", "pip", "setuptools", "wheel", "-i", PIP_INDEX])
    _run([*pip, "install", "-i", PIP_INDEX, "pyinstaller>=6.3"])

    dist = Path("/tmp/cad-browser-dist-linux")
    work = Path("/tmp/cad-browser-work-linux")
    for d in (dist, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    spec = ROOT / "packaging" / "cad-browser-desktop.spec"
    if not spec.is_file():
        raise SystemExit(f"missing spec: {spec}")

    _run(
        [
            py,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            str(spec),
        ]
    )

    built = dist / APP_EXE
    if not built.is_dir():
        raise SystemExit(f"PyInstaller output missing: {built}")
    entry = built / APP_EXE
    if not entry.is_file():
        raise SystemExit(f"missing binary: {entry}")
    entry.chmod(entry.stat().st_mode | 0o111)

    static = built / "_internal" / "cad_browser" / "static" / "index.html"
    if not static.is_file():
        raise SystemExit(f"bundled static missing: {static}")

    stamp = _utc_stamp()
    plat = _platform_tag()
    release = ROOT / "release" / f"{APP_ID}-desktop-{plat}-{stamp}"
    if release.exists():
        shutil.rmtree(release)
    shutil.copytree(built, release)

    _write_release_meta(
        release,
        platform=plat,
        entry=APP_EXE,
        notes="Built on Linux via native PyInstaller onedir",
        readme_lines=[
            "cad-browser (Linux)",
            "",
            "1. 解压本目录",
            "2. 运行: ./cad-browser --dir /path/to/assets [--port 6006]",
            "3. 浏览器打开 http://127.0.0.1:6006 （或你指定的端口）",
            "4. 无需安装 Python / Node / pip",
            "5. 只读展示 STL / STEP(+同名STL) / PDF / ZIP，不做 STEP 转换",
            "6. 请在本目录启动（不要 cd 进 _internal）",
            "",
        ],
    )

    tar_path = Path(str(release) + ".tar.gz")
    if tar_path.exists():
        tar_path.unlink()
    _run(["tar", "-C", str(release.parent), "-czf", str(tar_path), release.name])
    zip_path = _zip_release(release)

    print(f"release: {release}")
    print(f"tar.gz: {tar_path}")
    print(f"zip: {zip_path}")
    return release


def build_windows() -> Path:
    wine = _wine_bin()
    tools = ROOT / ".tools"
    prefix = Path(os.environ.get("WINEPREFIX") or (ROOT / ".wine-cad-browser"))
    prefix.parent.mkdir(parents=True, exist_ok=True)
    py_dir = _ensure_win_python(tools)
    env = _wine_env(prefix)

    _run([wine, "wineboot", "--init"], env=env)

    py_wine = _linux_to_wine_path(py_dir / "python.exe")
    get_pip = _linux_to_wine_path(tools / "get-pip.py")
    _run([wine, py_wine, get_pip, "-i", PIP_INDEX], env=env)
    try:
        _run([wine, py_wine, "-m", "pip", "--version"], env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            "pip not available in Wine embed Python after get-pip; "
            "re-download get-pip.py and ensure python*._pth has 'import site'"
        ) from e

    _run(
        [wine, py_wine, "-m", "pip", "install", "-i", PIP_INDEX, "pyinstaller>=6.3"],
        env=env,
    )

    dist = Path("/tmp/cad-browser-dist-win")
    work = Path("/tmp/cad-browser-work-win")
    for d in (dist, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    runner = _linux_to_wine_path(ROOT / "packaging" / "run_pyinstaller_wine.py")
    spec = _linux_to_wine_path(ROOT / "packaging" / "cad-browser-desktop-win.spec")
    _run(
        [
            wine,
            py_wine,
            runner,
            "--noconfirm",
            "--clean",
            "--distpath",
            _linux_to_wine_path(dist),
            "--workpath",
            _linux_to_wine_path(work),
            spec,
        ],
        env=env,
    )

    built = dist / APP_EXE
    if not built.is_dir():
        raise SystemExit(f"PyInstaller output missing: {built}")

    _postprocess_windows(built, py_dir)

    static = built / "_internal" / "cad_browser" / "static" / "index.html"
    if not static.is_file():
        raise SystemExit(f"bundled static missing: {static}")

    stamp = _utc_stamp()
    release = ROOT / "release" / f"{APP_ID}-desktop-windows-x64-{stamp}"
    if release.exists():
        shutil.rmtree(release)
    shutil.copytree(built, release)

    _write_release_meta(
        release,
        platform="windows-x64",
        entry=f"{APP_EXE}.exe",
        notes="Built on Linux via Wine + embeddable CPython + PyInstaller",
        readme_lines=[
            "cad-browser (Windows x64)",
            "",
            "1. Unzip this folder",
            "2. Run: cad-browser.exe --dir D:\\path\\to\\assets [--port 6006]",
            "3. Open http://127.0.0.1:6006 in a browser",
            "4. No need to install Python / Node",
            "5. Static preview only (STL / STEP+sibling STL / PDF / ZIP)",
            "",
        ],
    )

    zip_path = _zip_release(release)
    print(f"release: {release}")
    print(f"zip: {zip_path}")
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description="Build cad-browser packages")
    parser.add_argument(
        "--target",
        choices=["windows", "native", "linux"],
        default="native",
        help="windows = Wine cross-build; native/linux = current Linux host",
    )
    args = parser.parse_args()
    target = "native" if args.target == "linux" else args.target
    if target == "windows":
        build_windows()
    else:
        build_native()


if __name__ == "__main__":
    main()

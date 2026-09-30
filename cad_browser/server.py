"""Browse STL, STEP (sibling STL only), PDF and ZIP under a resource root."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

KIND = {
    ".stl": "mesh",
    ".step": "step",
    ".stp": "step",
    ".pdf": "drawing",
    ".zip": "archive",
}
SKIP_DIRS = {".ipynb_checkpoints", ".git", "static", "__pycache__", ".venv", "venv"}


def bundled_static() -> Path:
    """Resolve UI assets for source tree, onefile, or onedir (_internal)."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            base = Path(meipass)
            candidates.extend(
                [
                    base / "cad_browser" / "static",
                    base / "static",
                ]
            )
        # onedir: also try next to the executable
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "_internal" / "cad_browser" / "static",
                exe_dir / "cad_browser" / "static",
            ]
        )
    candidates.append(Path(__file__).resolve().parent / "static")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


class AppState:
    def __init__(self, root: Path, static: Path) -> None:
        self.root = root.resolve()
        self.static = static.resolve()
        if not self.root.is_dir():
            raise SystemExit(f"--dir 不是目录: {self.root}")
        if not (self.static / "index.html").is_file():
            raise SystemExit(f"找不到前端静态资源: {self.static}")


STATE: AppState | None = None


def rel_of(path: Path) -> str:
    assert STATE is not None
    return path.relative_to(STATE.root).as_posix()


def safe_path(raw: str) -> Path:
    assert STATE is not None
    raw = unquote(raw or "").strip().lstrip("/")
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise ValueError("bad path")
    path = (STATE.root / raw).resolve()
    if STATE.root not in path.parents and path != STATE.root:
        raise ValueError("bad path")
    if path.suffix.lower() not in KIND or not path.is_file():
        raise ValueError("bad path")
    return path


def sibling_stl(path: Path) -> Path | None:
    for ext in (".stl", ".STL"):
        candidate = path.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def zip_listing(path: Path, limit: int = 200) -> dict:
    entries: list[dict] = []
    with zipfile.ZipFile(path, "r") as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        infos.sort(key=lambda i: i.filename)
        truncated = len(infos) > limit
        for info in infos[:limit]:
            entries.append({"name": info.filename, "bytes": info.file_size})
    return {"entries": entries, "count": len(entries), "truncated": truncated}


def catalog() -> list[dict]:
    assert STATE is not None
    rows = []
    for path in sorted(STATE.root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(STATE.root).parts):
            continue
        kind = KIND.get(path.suffix.lower())
        if kind is None:
            continue
        preview = kind
        if kind == "step":
            preview = "sibling" if sibling_stl(path) else "none"
        rows.append(
            {
                "path": rel_of(path),
                "name": path.name,
                "dir": path.parent.relative_to(STATE.root).as_posix()
                if path.parent != STATE.root
                else ".",
                "kind": kind,
                "preview": preview,
                "bytes": path.stat().st_size,
            }
        )
    return rows


def mesh_for(path: Path) -> tuple[Path, str]:
    ext = path.suffix.lower()
    if ext == ".stl":
        return path, "stl"
    sibling = sibling_stl(path)
    if sibling is not None:
        return sibling, "sibling"
    raise RuntimeError("无同名 STL，无法预览 STEP（本服务只展示静态网格）")


class Handler(BaseHTTPRequestHandler):
    server_version = "cad-browser/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}", flush=True)

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str,
        filename: str | None = None,
        *,
        attachment: bool = False,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            disp = "attachment" if attachment else "inline"
            self.send_header(
                "Content-Disposition", f"{disp}; filename*=UTF-8''{quote(filename)}"
            )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _query_path(self) -> Path:
        qs = parse_qs(urlparse(self.path).query)
        return safe_path((qs.get("path") or [""])[0])

    def do_GET(self) -> None:  # noqa: N802
        assert STATE is not None
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in ("/", "/index.html"):
                data = (STATE.static / "index.html").read_bytes()
                self._send(200, data, "text/html; charset=utf-8")
                return
            if route.startswith("/static/"):
                name = route[len("/static/") :]
                if not name or "/" in name or ".." in name:
                    self._json(404, {"error": "not found"})
                    return
                file = STATE.static / name
                if not file.is_file():
                    self._json(404, {"error": "not found"})
                    return
                mime = (
                    "application/javascript"
                    if name.endswith(".js")
                    else "application/octet-stream"
                )
                self._send(200, file.read_bytes(), mime)
                return
            if route == "/api/catalog":
                self._json(200, {"files": catalog(), "root": str(STATE.root)})
                return
            if route == "/api/zip":
                path = self._query_path()
                if path.suffix.lower() != ".zip":
                    self._json(400, {"error": "不是 zip"})
                    return
                try:
                    listing = zip_listing(path)
                except zipfile.BadZipFile as exc:
                    raise RuntimeError("损坏的 zip") from exc
                listing["path"] = rel_of(path)
                listing["bytes"] = path.stat().st_size
                self._json(200, listing)
                return
            if route == "/raw":
                path = self._query_path()
                ext = path.suffix.lower()
                if ext == ".pdf":
                    mime = "application/pdf"
                elif ext == ".zip":
                    mime = "application/zip"
                else:
                    mime = "application/octet-stream"
                self._send(
                    200,
                    path.read_bytes(),
                    mime,
                    path.name,
                    attachment=(ext == ".zip"),
                )
                return
            if route == "/mesh":
                path = self._query_path()
                if path.suffix.lower() in {".pdf", ".zip"}:
                    self._json(400, {"error": "该文件没有网格"})
                    return
                mesh, source = mesh_for(path)
                body = mesh.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Mesh-Source", source)
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(404, {"error": "not found"})
        except ValueError:
            self._json(400, {"error": "路径无效"})
        except RuntimeError as exc:
            self._json(500, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cad-browser",
        description="浏览资源目录中的 STL / STEP(+同名STL) / PDF / ZIP（只读静态资源）",
    )
    p.add_argument("--dir", required=True, type=Path, help="资源根目录（递归扫描）")
    p.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    p.add_argument("--port", type=int, default=6006, help="端口（默认 6006）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    global STATE
    args = parse_args(argv)
    STATE = AppState(root=args.dir, static=bundled_static())
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"cad-browser {STATE.root} → http://{args.host}:{args.port}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)

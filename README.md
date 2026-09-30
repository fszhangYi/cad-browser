# cad-browser

只读静态资源浏览器：递归扫描 `--dir` 下的 **STL / STEP(+同名 STL) / PDF / ZIP**。  
不做 STEP 转换；打包后目标机无需 Python/pip。

打包流程对齐 `office_skills/scheme-a-linux-to-linux-desktop` 与 `scheme-a-linux-to-windows-desktop`（PyInstaller **onedir** + `release/`）。

## 开发运行

```bash
cd cad-browser
python3 -m cad_browser --dir /path/to/assets --port 6006
```

## 打包

```bash
./build.sh                     # Linux → release/cad-browser-desktop-linux-x64-<ts>/
./build.sh --target=windows    # 本机 Wine 交叉编译 → ...-windows-x64-<ts>/
```

产物含可执行文件、`_internal/`、`README.txt`、`BUILD_INFO.json`，并打 `tar.gz` / `zip`。

Windows 交叉编译需要：`wine64`、网络（拉 embeddable CPython + pip）。首次会写入 `.tools/win-python`、`.wine-cad-browser`。

## 使用（打包后）

```bash
# Linux
./cad-browser --dir /data/models --port 6006

# Windows
cad-browser.exe --dir D:\models --port 6006
```

浏览器打开 `http://127.0.0.1:6006`。

| 类型 | 行为 |
|------|------|
| `.stl` | Three.js 预览 |
| `.step` / `.stp` | 仅当同目录有同名 `.stl` 时预览 |
| `.pdf` | 内嵌打开 |
| `.zip` | 列目录 + 下载 |

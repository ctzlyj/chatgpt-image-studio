#!/usr/bin/env python3
"""Secret-safe local client for ChatGPT Image Studio."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import webbrowser
import zipfile


REPOSITORY = "https://github.com/ctzlyj/chatgpt-image-studio.git"
ARCHIVE = "https://github.com/ctzlyj/chatgpt-image-studio/archive/refs/heads/main.zip"
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
USER_AGENT = "chatgpt-web-image-skill/1"


class StudioError(RuntimeError):
    pass


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise StudioError("Image Studio 地址必须是本机 HTTP 回环地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise StudioError("Image Studio 地址格式不正确")
    return value.strip().rstrip("/")


def default_install_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ChatGPTImageStudio" / "app"


def is_project_root(path: Path) -> bool:
    return (path / "run.py").is_file() and (path / "studio" / "app.py").is_file() and (path / "requirements.lock.txt").is_file()


def find_project_root() -> Path | None:
    configured = os.environ.get("CHATGPT_IMAGE_STUDIO_HOME")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend(Path(__file__).resolve().parents)
    candidates.append(default_install_dir())
    for candidate in candidates:
        if is_project_root(candidate):
            return candidate.resolve()
    return None


def download_project(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise StudioError(f"安装目录已存在但不是有效项目：{destination}")
    git = shutil.which("git")
    if git:
        result = subprocess.run(
            [git, "clone", "--depth", "1", REPOSITORY, str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and is_project_root(destination):
            return destination.resolve()
        if destination.exists():
            shutil.rmtree(destination)
    with tempfile.TemporaryDirectory(prefix="chatgpt-image-studio-") as temporary:
        archive = Path(temporary) / "source.zip"
        try:
            with urlopen(Request(ARCHIVE, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
                archive.write_bytes(response.read())
            with zipfile.ZipFile(archive) as package:
                package.extractall(temporary)
            source = next(path for path in Path(temporary).iterdir() if path.is_dir() and path.name.startswith("chatgpt-image-studio-"))
            shutil.move(str(source), str(destination))
        except Exception as error:
            if destination.exists():
                shutil.rmtree(destination)
            raise StudioError("无法下载 Image Studio，请检查网络后重试") from error
    if not is_project_root(destination):
        raise StudioError("下载的 Image Studio 项目不完整")
    return destination.resolve()


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def compatible_python_launcher() -> list[str]:
    candidates = [[sys.executable]]
    if os.name == "nt" and shutil.which("py.exe"):
        candidates.append(["py.exe", "-3.12"])
    for name in ("python3.13", "python3.12"):
        if shutil.which(name):
            candidates.append([name])
    for candidate in candidates:
        result = subprocess.run(candidate + ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return candidate
    raise StudioError("需要 Python 3.12 或更高版本才能运行 Image Studio")


def prepare_runtime(root: Path) -> Path:
    python = venv_python(root)
    if not python.is_file():
        result = subprocess.run(compatible_python_launcher() + ["-m", "venv", str(root / ".venv")], cwd=root)
        if result.returncode != 0:
            raise StudioError("无法创建 Image Studio Python 环境")
    version = subprocess.run([str(python), "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"], cwd=root)
    if version.returncode != 0:
        raise StudioError("现有 Image Studio 虚拟环境低于 Python 3.12，请重新安装该本地环境")
    check = subprocess.run([str(python), str(root / "scripts" / "check_runtime.py")], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode != 0:
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(root / "requirements.lock.txt")],
            cwd=root,
        )
        if result.returncode != 0:
            raise StudioError("Image Studio 依赖安装失败")
        check = subprocess.run([str(python), str(root / "scripts" / "check_runtime.py")], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check.returncode != 0:
            raise StudioError("Image Studio 运行环境校验失败")
    return python


def start_service(root: Path, base_url: str) -> None:
    parsed = urlsplit(base_url)
    port = parsed.port or 80
    python = prepare_runtime(root)
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    stdout = open(data / "server.log", "ab", buffering=0)
    stderr = open(data / "server-error.log", "ab", buffering=0)
    kwargs = {"cwd": root, "stdout": stdout, "stderr": stderr}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen([str(python), str(root / "run.py"), "--port", str(port)], **kwargs)
    finally:
        stdout.close()
        stderr.close()
    (data / "server.pid").write_text(str(process.pid), encoding="ascii")


class StudioClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = validate_base_url(base_url)

    def request(self, method: str, path: str, payload: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 30):
        request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
        request = Request(self.base_url + path, data=payload, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read()
        except HTTPError as error:
            try:
                problem = json.loads(error.read().decode("utf-8", "replace"))
                detail = problem.get("detail", problem.get("error", f"HTTP {error.code}")) if isinstance(problem, dict) else f"HTTP {error.code}"
                if isinstance(detail, dict):
                    detail = detail.get("error") or json.dumps(detail, ensure_ascii=False)
            except Exception:
                detail = f"HTTP {error.code}"
            raise StudioError(str(detail)) from None
        except (URLError, TimeoutError, socket.timeout) as error:
            raise StudioError("无法连接本机 Image Studio 服务") from error
        if not data:
            return None
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise StudioError("Image Studio 返回了无法识别的结果") from error

    def bootstrap(self):
        result = self.request("GET", "/api/bootstrap", timeout=3)
        if not isinstance(result, dict) or result.get("application") != "chatgpt-image-studio":
            raise StudioError("该端口不是 Image Studio 服务")
        return result

    def ensure(self) -> dict:
        try:
            return self.bootstrap()
        except StudioError:
            root = find_project_root() or download_project(default_install_dir())
            start_service(root, self.base_url)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                time.sleep(0.5)
                if process_result := self._try_bootstrap():
                    return process_result
            raise StudioError(f"Image Studio 启动超时，请检查 {root / 'data' / 'server-error.log'}")

    def _try_bootstrap(self):
        try:
            return self.bootstrap()
        except StudioError:
            return None

    def page_headers(self) -> dict[str, str]:
        session = self.ensure()["session"]
        return {"Content-Type": "application/json", "x-studio-session": session}

    def api_headers(self, request_id: str | None = None) -> dict[str, str]:
        headers = self.page_headers()
        result = self.request("POST", "/api/local-api-key", b"{}", headers)
        output = {"Authorization": "Bearer " + result["api_key"]}
        if request_id:
            output["Idempotency-Key"] = request_id
        return output

    def configure(self, session_json: str, name: str = "Agent 导入账号") -> dict:
        if not session_json.strip():
            raise StudioError("没有收到 Session JSON")
        payload = json.dumps({"content": session_json, "name": name}, ensure_ascii=False).encode("utf-8")
        result = self.request("POST", "/api/accounts/import", payload, self.page_headers())
        return {"added": result.get("added", 0), "duplicates": result.get("duplicates", 0), "accounts": len(result.get("items", []))}

    def status(self) -> dict:
        result = self.request("GET", "/api/accounts", headers=self.page_headers())
        items = result.get("items", [])
        return {
            "service": "ready",
            "accounts": len(items),
            "enabled": sum(bool(item.get("enabled")) for item in items),
            "ready": sum(item.get("status") in {"ready", "unverified"} and item.get("enabled") for item in items),
            "states": [{"name": item.get("name"), "status": item.get("status"), "quota": item.get("quota")} for item in items],
        }

    def generate(self, prompt: str, count: int, model: str, size: str | None, upscale: int, request_id: str) -> dict:
        body = {"model": model, "prompt": prompt, "n": count, "response_format": "b64_json", "stream": False}
        if size:
            body["size"] = size
        if upscale > 1:
            body["upscale"] = upscale
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.api_headers(request_id)}
        return self.request("POST", "/v1/images/generations", payload, headers, timeout=930)

    def edit(self, prompt: str, images: list[Path], count: int, model: str, size: str | None, upscale: int, request_id: str) -> dict:
        boundary = "----ImageStudio" + uuid.uuid4().hex
        parts: list[bytes] = []
        fields = {"prompt": prompt, "model": model, "n": str(count), "response_format": "b64_json", "stream": "false"}
        if size:
            fields["size"] = size
        if upscale > 1:
            fields["upscale"] = str(upscale)
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8"))
        for path in images:
            if not path.is_file():
                raise StudioError(f"参考图不存在：{path}")
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="reference{path.suffix.lower()}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("ascii") + path.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("ascii"))
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **self.api_headers(request_id)}
        return self.request("POST", "/v1/images/edits", b"".join(parts), headers, timeout=930)


def clipboard_text() -> str:
    commands = []
    if os.name == "nt":
        commands.append(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"])
    elif sys.platform == "darwin":
        commands.append(["pbpaste"])
    else:
        commands.extend([["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]])
    for command in commands:
        if not shutil.which(command[0]):
            continue
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return result.stdout.decode("utf-8-sig")
    raise StudioError("无法读取剪贴板，请改用本机粘贴窗口")


def clear_clipboard_if_unchanged(expected: str) -> None:
    try:
        if clipboard_text() != expected:
            return
        if os.name == "nt":
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard -Value ''"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=b"", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("wl-copy"):
            subprocess.run(["wl-copy", "--clear"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=b"", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def session_dialog() -> str:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError as error:
        raise StudioError("当前 Python 没有图形界面支持，请使用 --clipboard") from error
    value = {"text": ""}
    window = tk.Tk()
    window.title("ChatGPT Image Studio - 导入 Session")
    window.geometry("720x440")
    tk.Label(window, text="粘贴完整 ChatGPT Session JSON。内容仅发送到本机服务，不会显示在终端。", anchor="w").pack(fill="x", padx=16, pady=(16, 8))
    editor = tk.Text(window, wrap="word", undo=False)
    editor.pack(fill="both", expand=True, padx=16, pady=8)

    def submit():
        content = editor.get("1.0", "end").strip()
        if not content:
            messagebox.showerror("无法导入", "请先粘贴完整 Session JSON")
            return
        value["text"] = content
        editor.delete("1.0", "end")
        window.destroy()

    tk.Button(window, text="安全导入", command=submit).pack(pady=(0, 16))
    window.protocol("WM_DELETE_WINDOW", window.destroy)
    editor.focus_set()
    window.mainloop()
    if not value["text"]:
        raise StudioError("已取消 Session 导入")
    return value["text"]


def read_prompt(arguments) -> str:
    if arguments.prompt_file:
        return Path(arguments.prompt_file).read_text(encoding="utf-8-sig").strip()
    return (arguments.prompt or "").strip()


def output_directory(value: str | None) -> Path:
    if value:
        directory = Path(value).expanduser().resolve()
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        directory = (Path.cwd() / "generated-images" / f"{stamp}-{uuid.uuid4().hex[:6]}").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_images(result: dict, directory: Path, request_id: str) -> dict:
    items = result.get("data") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        raise StudioError("Image Studio 没有返回图片；请先核对任务历史，不要直接重复提交")
    paths = []
    deliveries = []
    for index, item in enumerate(items, 1):
        encoded = item.get("b64_json") if isinstance(item, dict) else None
        if not isinstance(encoded, str):
            raise StudioError("图片结果格式不完整；请先核对任务历史，不要直接重复提交")
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise StudioError("图片结果无法解码") from error
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise StudioError("Image Studio 返回了非 PNG 图片")
        marker = item.get("upscale") if isinstance(item.get("upscale"), dict) else {}
        suffix = f"-upscaled{marker.get('factor')}x-{marker.get('backend')}" if marker.get("upscaled") else ""
        target = directory / f"image-{index:02d}{suffix}.png"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        paths.append(str(target))
        delivery = {"path": str(target), "native_size": item.get("native_size"), "size": item.get("size")}
        if marker.get("upscaled"):
            delivery["upscale"] = {"factor": marker.get("factor"), "backend": marker.get("backend"), "label": marker.get("label")}
            if marker.get("fallback"):
                delivery["upscale"]["fallback"] = marker["fallback"]
        deliveries.append(delivery)
    output = {"ok": True, "request_id": request_id, "batch_id": result.get("batch_id"), "images": paths, "deliveries": deliveries}
    if any("upscale" in item for item in deliveries):
        output["note"] = "放大件是非原生像素（网页原生尺寸见 native_size），交付时必须说明。"
    return output


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ChatGPT Image Studio Agent client")
    parser.add_argument("--base-url", default=os.environ.get("CHATGPT_IMAGE_STUDIO_URL", DEFAULT_BASE_URL))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ensure", help="install and start the local API if needed")
    configure = commands.add_parser("configure", help="securely import a full ChatGPT session JSON")
    sources = configure.add_mutually_exclusive_group()
    sources.add_argument("--clipboard", action="store_true", help="read the session from the local clipboard")
    sources.add_argument("--session-file", help="read a user-owned local file, or - for secure stdin")
    configure.add_argument("--name", default="Agent 导入账号")
    commands.add_parser("status", help="show redacted service and account status")
    commands.add_parser("open", help="open the optional local workbench")
    for name in ("generate", "edit"):
        command = commands.add_parser(name)
        prompts = command.add_mutually_exclusive_group(required=True)
        prompts.add_argument("--prompt")
        prompts.add_argument("--prompt-file")
        command.add_argument("--count", type=int, default=1, choices=range(1, 5), metavar="1..4")
        command.add_argument("--model", default="gpt-image-2.5")
        command.add_argument("--size")
        command.add_argument("--upscale", type=int, default=1, choices=range(1, 5), metavar="1..4", help="enlarge after generation; results are not native pixels")
        command.add_argument("--output-dir")
        command.add_argument("--request-id")
        if name == "edit":
            command.add_argument("--image", action="append", required=True)
    return parser


def main() -> int:
    arguments = make_parser().parse_args()
    client = StudioClient(arguments.base_url)
    try:
        if arguments.command == "ensure":
            bootstrap = client.ensure()
            output = {"ok": True, "service": client.base_url, "version": bootstrap.get("version")}
        elif arguments.command == "configure":
            used_clipboard = arguments.clipboard
            if arguments.clipboard:
                session_json = clipboard_text()
            elif arguments.session_file == "-":
                session_json = sys.stdin.read()
            elif arguments.session_file:
                session_json = Path(arguments.session_file).expanduser().read_text(encoding="utf-8-sig")
            else:
                session_json = session_dialog()
            output = {"ok": True, **client.configure(session_json, arguments.name)}
            if used_clipboard:
                clear_clipboard_if_unchanged(session_json)
            session_json = ""
        elif arguments.command == "status":
            output = {"ok": True, **client.status()}
        elif arguments.command == "open":
            client.ensure()
            webbrowser.open(client.base_url)
            output = {"ok": True, "opened": client.base_url}
        else:
            prompt = read_prompt(arguments)
            if not prompt:
                raise StudioError("提示词不能为空")
            request_id = arguments.request_id or uuid.uuid4().hex
            directory = output_directory(arguments.output_dir)
            if arguments.command == "generate":
                result = client.generate(prompt, arguments.count, arguments.model, arguments.size, arguments.upscale, request_id)
            else:
                images = [Path(value).expanduser().resolve() for value in arguments.image]
                if not 1 <= len(images) <= 12:
                    raise StudioError("参考图数量必须是 1 至 12 张")
                result = client.edit(prompt, images, arguments.count, arguments.model, arguments.size, arguments.upscale, request_id)
            output = save_images(result, directory, request_id)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (StudioError, OSError, UnicodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

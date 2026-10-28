from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RECORD_DIR = os.path.join(APP_DIR, ".fused", "data", "addons")

ADDONS = {
    "smart-select": {"apple_silicon": True, "modules": ("mlx_sam", "mlx.core", "huggingface_hub")},
    "upscale": {"apple_silicon": True, "modules": ("mflux.models.seedvr2", "mlx.core", "huggingface_hub")},
    "background-removal": {"apple_silicon": False, "modules": ("Vision", "Quartz")},
}

ENGINE_CALLS = {
    "smart-select": {"sam_status", "ensure_sam", "smart_mask", "start_download"},
    "upscale": {"upscaler_status", "unload_upscaler", "start_upscale", "poll", "cancel", "start_download"},
}

_ENV_KEYS = ("PYTHONHOME", "PYTHONPATH")


def _darwin_major() -> int:
    try:
        return int(platform.release().split(".")[0])
    except ValueError:
        return 0


def unsupported_reason(name: str) -> str:
    if sys.platform != "darwin":
        return "needs macOS"
    if ADDONS[name]["apple_silicon"] and platform.machine() != "arm64":
        return "needs a Mac with Apple Silicon"
    if _darwin_major() < 23:
        return "needs macOS 14 or newer"
    return ""


def _record_path(name: str) -> str:
    return os.path.join(RECORD_DIR, name + ".json")


def record(name: str) -> dict:
    missing = []
    for module in ADDONS[name]["modules"]:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    ready = not missing and not unsupported_reason(name)
    data = {"python": sys.executable, "ready": ready, "missing": missing,
            **{key.lower(): os.environ.get(key) for key in _ENV_KEYS}}
    os.makedirs(RECORD_DIR, exist_ok=True)
    temporary = _record_path(name) + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(data, handle)
    os.replace(temporary, _record_path(name))
    reason = unsupported_reason(name)
    message = reason or ("" if ready else "Installed, but missing " + ", ".join(missing))
    return {"ok": ready, "addon": name, "message": message}


def installed(name: str) -> dict | None:
    try:
        with open(_record_path(name)) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not data.get("ready") or not os.path.isfile(data.get("python") or ""):
        return None
    return data


def status() -> dict:
    report = {}
    for name in ADDONS:
        reason = unsupported_reason(name)
        report[name] = {"supported": not reason, "reason": reason,
                        "installed": not reason and installed(name) is not None}
    return report


class AddonMissing(RuntimeError):
    pass


class _Child:
    def __init__(self, name: str, data: dict):
        env = {key: value for key, value in os.environ.items()
               if key not in _ENV_KEYS + ("PYTHONEXECUTABLE", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")}
        for key in _ENV_KEYS:
            if data.get(key.lower()):
                env[key] = data[key.lower()]
        self.name = name
        self.python = data["python"]
        self.process = subprocess.Popen(
            [self.python, os.path.join(APP_DIR, "addon_env.py"), name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=APP_DIR, env=env,
            text=True, bufsize=1,
        )
        self.lock = threading.Lock()
        self.waiting: dict[int, tuple] = {}
        self.next_id = 0
        threading.Thread(target=self._read, name=f"addon-{name}", daemon=True).start()

    def alive(self) -> bool:
        return self.process.poll() is None

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            slot = self.waiting.pop(reply.get("id"), None)
            if slot is not None:
                slot[1].update(reply)
                slot[0].set()
        with self.lock:
            for event, box in self.waiting.values():
                box.setdefault("error", f"The {self.name} add-on stopped")
                event.set()
            self.waiting.clear()

    def call(self, fn: str, kwargs: dict, timeout: float = 55.0):
        event, box = threading.Event(), {}
        with self.lock:
            if not self.alive():
                raise RuntimeError(f"The {self.name} add-on stopped")
            self.next_id += 1
            key = self.next_id
            self.waiting[key] = (event, box)
            self.process.stdin.write(json.dumps({"id": key, "fn": fn, "kwargs": kwargs}) + "\n")
            self.process.stdin.flush()
        if not event.wait(timeout):
            self.waiting.pop(key, None)
            raise TimeoutError(f"The {self.name} add-on did not answer in time")
        if "error" in box:
            raise RuntimeError(box["error"])
        return box.get("result")

    def stop(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass


_children: dict[str, _Child] = {}
_children_lock = threading.Lock()


def engine_call(name: str, fn: str, required: bool = False, spawn: bool = True, **kwargs):
    data = None if unsupported_reason(name) else installed(name)
    with _children_lock:
        child = _children.get(name)
        if child is not None and (data is None or child.python != data["python"]):
            child.stop()
            _children.pop(name, None)
            child = None
        if data is not None and (child is None or not child.alive()) and spawn:
            child = _children[name] = _Child(name, data)
    if child is None or not child.alive():
        if required:
            raise AddonMissing(f"The {name} add-on " + (unsupported_reason(name) or "is not installed -- use Install in its panel"))
        return None
    return child.call(fn, kwargs)


def _serve(name: str) -> None:
    import engine

    allowed = ENGINE_CALLS[name]
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    lock = threading.Lock()

    def handle(request: dict) -> None:
        reply = {"id": request.get("id")}
        try:
            if request.get("fn") not in allowed:
                raise ValueError(f"Unknown call: {request.get('fn')!r}")
            reply["result"] = getattr(engine, request["fn"])(**(request.get("kwargs") or {}))
        except BaseException as error:
            reply["error"] = str(error) or type(error).__name__
        with lock:
            out.write(json.dumps(reply) + "\n")
            out.flush()

    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        threading.Thread(target=handle, args=(request,), daemon=True).start()


if __name__ == "__main__":
    _serve(sys.argv[1])

"""Warm-worker entry point, declared by `[tool.fused-render.app] main =`.

Every call here must return in well under a minute: the shipped worker's HTTP
handler times out at 65 s, and a render can still run past that. So this
file only ever starts work and reads status -- `engine.py` owns the threads and
keeps the model resident between calls.
"""

from __future__ import annotations

import json

from addon_env import engine_call


def main(action: str = "status", session_id: str = "", job_id: str = "",
         source: str = "", points: str = "[]", labels: str = "[]",
         scale: str = "2x", softness: float = 0.0, which: str = "") -> dict:
    if action == "status":
        return {"upscaler": engine_call("upscale", "upscaler_status"),
                "sam": engine_call("smart-select", "sam_status")}

    if action == "warmup":
        # Fire-and-forget: returns the current state, does not wait for weights.
        # Loads SAM only if it is already on disk -- never downloads.
        return {"sam": engine_call("smart-select", "ensure_sam"),
                "upscaler": engine_call("upscale", "upscaler_status")}

    if action == "download":
        # Only ever sent from a Download button the user pressed.
        addon = {"upscaler": "upscale", "sam": "smart-select"}.get(which)
        if addon is None:
            raise ValueError(f"Unknown download: {which!r}")
        engine_call(addon, "start_download", required=True, which=which)
        return main("status")

    if action == "unload_upscaler":
        return engine_call("upscale", "unload_upscaler", spawn=False) or {"freed": True, "loaded": False}

    if action == "sam_status":
        return engine_call("smart-select", "sam_status", required=True)

    if action == "smart_mask":
        return engine_call(
            "smart-select", "smart_mask", required=True,
            session_id=session_id, source=source,
            points=_as_list(points), labels=_as_list(labels),
        )

    if action == "upscale_start":
        started = engine_call(
            "upscale", "start_upscale", required=True,
            session_id=session_id, source=source, scale=str(scale),
            softness=float(softness), job_id=job_id,
        )
        return {**started, "upscaler": engine_call("upscale", "upscaler_status")}

    if action == "edit_poll":
        return engine_call("upscale", "poll", spawn=False, job_id=job_id) or _no_job()

    if action == "edit_cancel":
        return engine_call("upscale", "cancel", spawn=False, job_id=job_id) or _no_job()

    raise ValueError(f"Unknown action: {action!r}")


def _no_job() -> dict:
    return {"state": "unknown", "stage": "No such job", "progress": 0.0}


def _as_list(value) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    return json.loads(value or "[]")

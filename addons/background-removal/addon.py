from __future__ import annotations

import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)

import addon_env


def main(action: str = "setup", session_id: str = "", source: str = "",
         x: float = 0.5, y: float = 0.5) -> dict:
    if action == "setup":
        return addon_env.record("background-removal")
    if action not in ("remove_background", "subject_at_point"):
        raise ValueError(f"Unknown action: {action!r}")
    import photo_editor

    return photo_editor.main(action=action, session_id=session_id, source=source, x=x, y=y)

from __future__ import annotations

import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)

import addon_env


def main() -> dict:
    return addon_env.record("smart-select")

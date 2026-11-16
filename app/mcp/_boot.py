# -*- coding: utf-8 -*-
"""MCP启动入口：统一项目目录，并将协议外日志写到stderr。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# app/mcp/_boot.py → app/mcp → app → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_booted = False


def ensure_project_root(verbose: bool = False) -> Path:
    """切到项目根并把项目根放进 sys.path（幂等）。"""
    global _booted
    if not _booted:
        os.chdir(PROJECT_ROOT)
        root = str(PROJECT_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        _booted = True
    if verbose:
        log(f"[mcp] 工作目录 = {PROJECT_ROOT}")
    return PROJECT_ROOT


def log(*args) -> None:
    """所有诊断输出走 **stderr**（stdout 留给协议流）。"""
    print(*args, file=sys.stderr, flush=True)

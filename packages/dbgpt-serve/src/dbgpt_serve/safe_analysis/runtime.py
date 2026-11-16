"""Composition root: the only place that assembles configuration and services."""

import os

from .config import AnalysisSettings
from .contracts import AnalysisError


def build_service(system_app=None):
    path = os.environ.get("DATA_AGENT_CONFIG")
    if not path:
        raise AnalysisError("安全分析尚未启用，请设置 DATA_AGENT_CONFIG")
    from .service import AnalysisService

    return AnalysisService(AnalysisSettings(path), system_app)

"""Composition root: the only place that assembles configuration and services."""

import os
from pathlib import Path

from .config import AnalysisSettings
from .contracts import AnalysisError


def build_service(system_app=None):
    path = os.environ.get("DATA_AGENT_CONFIG")
    if not path:
        raise AnalysisError("安全分析尚未启用，请设置 DATA_AGENT_CONFIG")
    from .management import ModelSettings
    from .service import AnalysisService

    settings = AnalysisSettings(path)
    model_path = Path(
        os.environ.get("DATA_AGENT_MODEL_SETTINGS")
        or "data/data-agent-model.local.json"
    )
    model_settings = ModelSettings(model_path)
    retrieval_models = {
        kind: ModelSettings(
            model_path.with_name(f"{model_path.stem}.{kind}.json"), kind=kind
        )
        for kind in ("embedding", "rerank")
    }
    service = AnalysisService(
        settings,
        system_app,
        retrieval_models=retrieval_models,
        knowledge_root=Path(
            os.environ.get("DATA_AGENT_KNOWLEDGE_DIR", "data/data-agent-knowledge")
        ),
    )
    service.model_settings = model_settings
    service.model.overrides = model_settings
    return service

"""Model settings and the adapter to the framework's data source service."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_app.openapi.api_v1 import workbench_management_api as management_api
from dbgpt_serve.safe_analysis import management
from dbgpt_serve.safe_analysis.contracts import AnalysisError
from dbgpt_serve.safe_analysis.management import ModelConnection, ModelSettings
from dbgpt_serve.safe_analysis.model import DataAgentModel

OWNER = "synthetic-management-test-token-123456"
HEADERS = {"Authorization": f"Bearer {OWNER}"}
MODEL = {
    "base_url": "https://example.invalid/v1/",
    "model": "test-model",
    "api_key": "synthetic-key-123",
}


@pytest.fixture
def management_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_AGENT_TOKEN", OWNER)
    monkeypatch.delenv("DATA_AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("DATA_AGENT_ENCRYPT_KEY", raising=False)
    app = FastAPI()
    app.include_router(management_api.router)
    store = ModelSettings(tmp_path / "model.json")
    app.state.safe_analysis_service = SimpleNamespace(
        settings=SimpleNamespace(model="startup-model"), model_settings=store
    )
    with TestClient(app) as client:
        yield client, store


def test_model_activation_reset_and_credential_handling(management_app, monkeypatch):
    client, store = management_app

    async def probe(config, system, prompt, *, probe):
        assert probe and not system and config.model == MODEL["model"]
        assert config.api_key.get_secret_value() == MODEL["api_key"]

    monkeypatch.setattr(management_api, "complete", probe)
    url = "/v1/safe-analysis/manage/model"
    assert client.get(url).status_code == 403
    response = client.post(f"{url}?test_only=true", json=MODEL, headers=HEADERS)
    assert response.is_success and not store.path.exists()
    response = client.post(url, json=MODEL, headers=HEADERS)
    assert response.json()["active"]
    assert MODEL["api_key"] not in response.text
    assert MODEL["api_key"] not in store.path.read_text()
    assert ModelSettings(store.path).current.model == MODEL["model"]
    assert (
        ModelSettings(store.path).current.api_key.get_secret_value() == MODEL["api_key"]
    )
    response = client.post(
        url,
        json={**MODEL, "base_url": "https://another.invalid", "api_key": ""},
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert store.current.base_url == "https://example.invalid/v1"
    assert client.delete(url, headers=HEADERS).json()["model"] == "startup-model"
    assert not store.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_suffix", ["", "/chat/completions/"])
@pytest.mark.parametrize(
    ("base_url", "trust_env"),
    [("https://example.invalid/v1", True), ("http://localhost:11434/v1", False)],
)
async def test_model_adapter_sends_active_model(
    monkeypatch, tmp_path, url_suffix, base_url, trust_env
):
    store = ModelSettings(tmp_path / "model.json")
    url = f"{base_url}{url_suffix}"
    store.current = store.prepare(ModelConnection(**{**MODEL, "base_url": url}))
    model = DataAgentModel(None, "not used")
    model.overrides = store

    def respond(request):
        assert str(request.url) == f"{base_url}/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + MODEL["api_key"]
        body = json.loads(request.content)
        assert body["model"] == MODEL["model"]
        if body["max_tokens"] == 64:
            assert body["messages"] == [{"role": "user", "content": "probe"}]
        else:
            assert body["messages"] == [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "question"},
            ]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "reply"}}]}
        )

    client_type = httpx.AsyncClient
    timeouts = []

    def mock_client(**kwargs):
        assert kwargs["trust_env"] is trust_env
        assert kwargs["follow_redirects"] is False
        timeouts.append(kwargs["timeout"])
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(management.httpx, "AsyncClient", mock_client)
    assert await model._generate("rules", "question") == "reply"
    assert await management.complete(store.current, "", "probe", probe=True) == "reply"
    assert timeouts == [45, 30]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/v1",
        "https://a:secret@example.invalid",
        "https://example.invalid/v1?key=abc",
        "https://example.invalid/v1#fragment",
    ],
)
def test_model_rejects_insecure_or_malformed_origin(tmp_path, url):
    with pytest.raises(AnalysisError):
        ModelSettings(tmp_path / "model.json").prepare(
            ModelConnection(**{**MODEL, "base_url": url})
        )


def test_model_endpoint_must_match_category(tmp_path):
    store = ModelSettings(tmp_path / "embedding.json", kind="embedding")
    with pytest.raises(AnalysisError, match="请填到「重排模型」"):
        store.prepare(
            ModelConnection(
                **{**MODEL, "base_url": "https://example.invalid/v1/rerank"}
            )
        )


def test_same_endpoint_can_reuse_key_when_switching_url_format(tmp_path):
    store = ModelSettings(tmp_path / "rerank.json", kind="rerank")
    store.current = store.prepare(ModelConnection(**MODEL))
    value = store.prepare(
        ModelConnection(
            base_url="https://example.invalid/v1/rerank",
            model=MODEL["model"],
        )
    )
    assert value.api_key.get_secret_value() == MODEL["api_key"]


def test_connection_adapter_uses_native_service_and_masks_password(
    management_app, monkeypatch
):
    client, _ = management_app
    records = {
        1: SimpleNamespace(
            id=1,
            db_name="sales",
            type="mysql",
            description="",
            params={"database": "sales", "password": "native-secret"},
        ),
    }
    calls = []
    resource = [
        {"param_name": "database", "nested_fields": None},
        {"param_name": "password", "nested_fields": None, "default_value": "hidden"},
    ]
    service = SimpleNamespace(
        get_list=lambda: list(records.values()),
        get=lambda pk: records.get(int(pk)),
        datasource_types=lambda: SimpleNamespace(
            model_dump=lambda: {
                "types": [
                    {"name": "mysql", "parameters": [dict(item) for item in resource]}
                ],
            }
        ),
        datasource_manager=SimpleNamespace(
            _create_parameters=lambda body: SimpleNamespace(
                persisted_state=lambda: {"db_name": body.params["database"]},
            )
        ),
        test_connection=lambda body: calls.append(("test", body.params)) or True,
        create=lambda body: calls.append(("create", body.params)),
        update=lambda body: calls.append(("update", body.params)),
        refresh=lambda pk: calls.append(("refresh", pk)),
        delete=lambda pk: calls.append(("delete", pk)),
    )
    monkeypatch.setattr(management_api, "native_datasources", lambda: service)
    url = "/v1/safe-analysis/manage/datasources"
    assert client.get(url).status_code == 403
    response = client.get(url, headers=HEADERS)
    assert response.json()["connections"][0]["params"] == {"database": "sales"}
    assert "native-secret" not in response.text and "hidden" not in response.text
    body = {"type": "mysql", "params": {"database": "sales"}}
    assert client.post(f"{url}?test_only=true", headers=HEADERS, json=body).is_success
    assert [name for name, _ in calls] == ["test"]
    assert client.post(url, headers=HEADERS, json=body).is_success
    assert client.post(url, headers=HEADERS, json={**body, "id": 1}).is_success
    assert calls[-1] == ("update", {"database": "sales", "password": "native-secret"})
    assert client.post(f"{url}/1/refresh", headers=HEADERS).is_success
    assert client.delete(f"{url}/1", headers=HEADERS).is_success
    assert calls[-2:] == [("refresh", "1"), ("delete", "1")]

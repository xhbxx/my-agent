"""设置不回显密钥，保存立即生效，网页建库复用真实索引流程。"""

import threading
import time

from dotenv import dotenv_values
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def settings_client(tmp_path, monkeypatch, store):
    from app import config, web_settings
    from app.knowledge import jobs
    from app.server import app

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".env")
    monkeypatch.setattr(config, "embedding_identity", lambda: "test-hash")
    monkeypatch.setattr(web_settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(web_settings, "STORE_DIR", store.root)
    monkeypatch.setattr(web_settings, "build", jobs.IndexBuild())
    monkeypatch.setattr(jobs, "get_store", lambda: store)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "reference.md").write_text(
        "# 测试文献\n这是一段用于工程验证的文献正文。", encoding="utf-8"
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def save(client):
    return client.put(
        "/api/settings",
        json={
            "deepseek_api_key": "sk-test-deepseek-only",
            "siliconflow_api_key": "sk-test-siliconflow-only",
        },
    )


def wait_finished(client):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = client.get("/api/knowledge/build").json()
        if not state["running"]:
            return state
        time.sleep(0.02)
    raise AssertionError("入库任务没有结束")


def test_save_preserves_other_settings_and_never_returns_keys(settings_client):
    from app import config, embed, llm
    from app.knowledge import runtime

    config.CONFIG_PATH.write_text("OTHER_SETTING='keep-me'\n", encoding="utf-8")
    response = save(settings_client)
    assert response.status_code == 200
    assert response.json()["deepseek_configured"]
    assert "sk-test" not in response.text
    assert "sk-test" not in settings_client.get("/api/settings").text
    values = dotenv_values(config.CONFIG_PATH)
    assert values["OTHER_SETTING"] == "keep-me"
    assert config.api_key("DEEPSEEK_API_KEY") == "sk-test-deepseek-only"
    assert embed._embeddings is None and llm._llm is None and runtime._store is None
    settings_client.put("/api/settings", json={"deepseek_api_key": ""})
    assert config.api_key("DEEPSEEK_API_KEY") == "sk-test-deepseek-only"


def test_settings_save_three_services_without_echoing_keys(settings_client):
    payload = {}
    for name in ("llm", "embedding", "rerank"):
        payload.update(
            {
                f"{name}_base_url": f"https://{name}.example/v1",
                f"{name}_model": f"custom-{name}",
                f"{name}_api_key": f"sk-private-{name}",
            }
        )
    response = settings_client.put("/api/settings", json=payload)
    assert response.status_code == 200
    assert "sk-private" not in response.text
    for name, model in response.json()["models"].items():
        assert model["model"] == f"custom-{name}"
        assert model["api_key_configured"]
    assert "sk-private" not in settings_client.get("/api/settings").text


@pytest.mark.parametrize(
    "payload",
    [
        {"deepseek_api_key": "sk-secret\nINJECTED=value"},
        {"deepseek_api_key": "sk-secret" * 200},
        {"deepseek_api_key": ["sk-secret"]},
    ],
)
def test_invalid_settings_never_echo_input(settings_client, payload):
    from app import config

    response = settings_client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert "sk-secret" not in response.text
    assert not config.CONFIG_PATH.exists()


def test_cross_origin_and_foreign_host_cannot_modify_settings(settings_client):
    response = settings_client.put(
        "/api/settings",
        json={"deepseek_api_key": "sk-test"},
        headers={"Origin": "https://unrelated.example"},
    )
    assert response.status_code == 403
    assert (
        settings_client.post("/api/knowledge/build", headers={"Origin": "null"}).status_code == 403
    )
    assert (
        settings_client.get("/api/settings", headers={"Host": "unrelated.example"}).status_code
        == 403
    )


def test_build_requires_configuration_and_publishes_real_index(settings_client, store):
    assert settings_client.post("/api/knowledge/build").status_code == 400
    assert save(settings_client).status_code == 200
    assert settings_client.post("/api/knowledge/build").status_code == 202
    state = wait_finished(settings_client)
    assert state["status"] == "complete"
    assert state["processed"] == state["total"] == 1
    assert state["index_ready"]
    assert store.snapshot()[0][0].metadata["source"] == "reference.md"
    settings_client.post("/api/knowledge/build")
    assert wait_finished(settings_client)["status"] == "complete"
    assert len(store.snapshot()[0]) == 1


def test_running_build_reuses_task_and_blocks_key_changes(settings_client, store, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = store.embeddings.embed_documents
    calls = []

    def slow_embeddings(texts):
        calls.append(texts)
        entered.set()
        assert release.wait(8)
        return original(texts)

    monkeypatch.setattr(store.embeddings, "embed_documents", slow_embeddings)
    save(settings_client)
    try:
        settings_client.post("/api/knowledge/build")
        assert entered.wait(8)
        assert settings_client.post("/api/knowledge/build").status_code == 202
        assert save(settings_client).status_code == 409
    finally:
        release.set()
        wait_finished(settings_client)
    assert len(calls) == 1


def test_failed_build_hides_keys_and_can_retry(settings_client, store, monkeypatch):
    save(settings_client)
    original = store.embeddings.embed_documents

    def unavailable(texts):
        raise RuntimeError("401 invalid sk-test-siliconflow-only")

    monkeypatch.setattr(store.embeddings, "embed_documents", unavailable)
    settings_client.post("/api/knowledge/build")
    state = wait_finished(settings_client)
    assert state["status"] == "failed"
    assert "401" in state["message"]
    assert "sk-test" not in str(state)
    monkeypatch.setattr(store.embeddings, "embed_documents", original)
    settings_client.post("/api/knowledge/build")
    assert wait_finished(settings_client)["index_ready"]


def test_invalid_token_stops_before_processing_other_documents(settings_client, store, monkeypatch):
    from app import web_settings

    for number in range(6):
        (web_settings.BASE_DIR / "data" / f"extra-{number}.md").write_text(
            "这是一份额外的工程测试文献资料。", encoding="utf-8"
        )
    calls = []

    class InvalidToken(RuntimeError):
        status_code = 401

    def rejected(texts):
        calls.append(texts)
        raise InvalidToken("Token is invalid. sk-test-siliconflow-only")

    monkeypatch.setattr(store.embeddings, "embed_documents", rejected)
    save(settings_client)
    settings_client.post("/api/knowledge/build")
    state = wait_finished(settings_client)
    assert state["status"] == "failed"
    assert state["total"] == 7
    assert state["processed"] == state["failed"] == len(calls) == 1
    assert "向量" in state["message"] and "设置" in state["message"]
    assert "401" in state["message"]
    assert "sk-test" not in str(state)


def test_invalid_model_stops_build_before_other_documents(settings_client, store, monkeypatch):
    from app import web_settings

    for number in range(6):
        (web_settings.BASE_DIR / "data" / f"extra-{number}.md").write_text(
            "这是一份额外的工程测试文献资料。", encoding="utf-8"
        )
    calls = []

    class InvalidModel(RuntimeError):
        status_code = 422

    def rejected(texts):
        calls.append(texts)
        raise InvalidModel("Input tag 'BAAI/bge-m3' does not match Jina models")

    monkeypatch.setattr(store.embeddings, "embed_documents", rejected)
    save(settings_client)
    settings_client.post("/api/knowledge/build")
    state = wait_finished(settings_client)
    assert state["status"] == "failed"
    assert state["total"] == 7
    assert state["processed"] == state["failed"] == len(calls) == 1
    assert "422" in state["message"]
    assert "模型名称" in state["message"]


def test_launcher_reuses_running_service(tmp_path, monkeypatch):
    from app import launcher

    monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
    monkeypatch.setattr(launcher, "service_ready", lambda port: port == 7862)
    monkeypatch.setattr(launcher, "page_url", lambda port: f"http://127.0.0.1:{port}/settings")

    def unexpected_process(*args, **kwargs):
        raise AssertionError("已有服务时不应再次创建进程")

    monkeypatch.setattr(launcher.subprocess, "Popen", unexpected_process)
    assert launcher.launch(open_browser=False) == "http://127.0.0.1:7862/settings"

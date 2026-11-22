import queue
import threading

from fastapi.testclient import TestClient


def test_stop_prevents_next_step_and_closes_generator():
    from app import server

    events = queue.Queue()
    events.cancelled = threading.Event()
    calls = []

    def generate():
        try:
            calls.append("first")
            events.cancelled.set()
            yield {"type": "delta", "text": "未交付"}
            calls.append("second")
        finally:
            calls.append("closed")

    server._run_stream(generate(), events)
    assert calls == ["first", "closed"]
    assert events.get_nowait() is server._END
    assert events.empty()


def test_disconnect_keeps_delivered_text_and_requests_stop():
    from app import server

    events = queue.Queue()
    events.cancelled = threading.Event()
    events.put({"type": "delta", "text": "已交付内容"})
    saved = []
    stream = server._sse_stream(events, lambda *values: saved.append(values))
    assert "已交付内容" in next(stream)
    stream.close()
    assert events.cancelled.is_set()
    assert saved == [("已交付内容", [], {})]


def test_stop_endpoint_and_worker_cleanup(chat_store):
    from app import server

    conversation = chat_store.create_conversation("停止测试")
    events = queue.Queue()
    started = threading.Event()
    finished = threading.Event()

    def worker(output):
        started.set()
        output.cancelled.wait(3)
        finished.set()

    server._start_generation(conversation["id"], events, worker)
    assert started.wait(2)
    with TestClient(server.app) as client:
        response = client.post(f"/api/conversations/{conversation['id']}/stop")
    assert response.json() == {"stopped": True}
    assert finished.wait(2)
    assert events.cancelled.is_set()

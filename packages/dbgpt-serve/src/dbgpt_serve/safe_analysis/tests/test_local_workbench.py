import pytest
from starlette.requests import Request

from dbgpt_app.openapi.api_v1.workbench_access import workbench_token
from dbgpt_serve.safe_analysis.contracts import QueryResult
from dbgpt_serve.safe_analysis.results import chart_spec


def browser_request(
    host="127.0.0.1:5670",
    ip="127.0.0.1",
    site="same-origin",
    origin=None,
    method="GET",
    extra_headers=(),
):
    headers = [
        (b"host", host.encode()),
        (b"sec-fetch-site", site.encode()),
        (b"x-data-agent-local", b"1"),
        *extra_headers,
    ]
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "scheme": "http",
            "path": "/",
            "headers": headers,
            "method": method,
            "client": (ip, 11111),
            "server": ("127.0.0.1", 5670),
        }
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ip": "192.0.2.10"},
        {"host": "not-local.example:5670"},
        {"site": "cross-site"},
        {"origin": "http://not-local.example"},
        {"method": "POST"},
        {"extra_headers": [(b"x-forwarded-for", b"127.0.0.1")]},
    ],
)
def test_local_access_does_not_accept_remote_or_cross_site(monkeypatch, kwargs):
    monkeypatch.setenv("DATA_AGENT_LOCAL_UI", "1")
    monkeypatch.setenv("DATA_AGENT_TOKEN", "internal-bearer")
    assert workbench_token(browser_request(**kwargs), "") == ""


def test_local_default_is_off_and_same_origin_works_when_enabled(monkeypatch):
    monkeypatch.delenv("DATA_AGENT_LOCAL_UI", raising=False)
    monkeypatch.setenv("DATA_AGENT_TOKEN", "internal-bearer")
    request = browser_request()
    assert workbench_token(request, "") == ""
    assert workbench_token(request, "Bearer remote-bearer") == "remote-bearer"
    monkeypatch.setenv("DATA_AGENT_LOCAL_UI", "1")
    assert workbench_token(request, "") == "internal-bearer"
    same_origin = browser_request(method="POST", origin="http://127.0.0.1:5670")
    assert workbench_token(same_origin, "") == "internal-bearer"
    assert workbench_token(same_origin, "Basic wrong") == ""


def test_chart_uses_line_for_dates_and_bar_for_categories():
    daily = QueryResult(
        ["date", "total"], [["2026-01-02", 2], ["2026-01-01", 1]], False, 1
    )
    chart = chart_spec(daily)
    assert chart["type"] == "line" and chart["data"][0] == ["2026-01-01", 1]
    regional = QueryResult(["region", "total"], [["East", 2], ["West", 1]], False, 1)
    assert chart_spec(regional)["type"] == "bar"

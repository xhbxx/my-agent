"""Resolve workbench access without asking the local browser for a secret."""

import os

from fastapi import Request


def workbench_token(request: Request, authorization: str) -> str:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        return token if scheme.lower() == "bearer" else ""
    if os.environ.get("DATA_AGENT_LOCAL_UI") != "1":
        return ""
    if (
        not request.client
        or request.client.host not in {"127.0.0.1", "::1"}
        or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or "forwarded" in request.headers
        or any(key.startswith("x-forwarded-") for key in request.headers)
        or request.headers.get("x-data-agent-local") != "1"
        or request.headers.get("sec-fetch-site") != "same-origin"
    ):
        return ""
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        return ""
    if request.method not in {"GET", "HEAD"} and not origin:
        return ""
    return os.environ.get("DATA_AGENT_TOKEN", "")

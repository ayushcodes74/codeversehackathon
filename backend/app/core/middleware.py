"""Common HTTP middleware for CORS, request ID propagation, and access logging."""

import logging
import time
import uuid
from typing import MutableMapping

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import AppSettings
from app.core.logging import request_id_ctx

logger = logging.getLogger("app.request")


class RequestContextMiddleware:
    """ASGI middleware that binds a correlation request ID and logs request timing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract_or_generate_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_ctx.set(request_id)

        start = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers = [
                    (k, v) for k, v in headers if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            method = scope.get("method", "-")
            path = scope.get("path", "-")
            logger.info("%s %s -> %s (%.2f ms)", method, path, status_code, duration_ms)
            request_id_ctx.reset(token)

    @staticmethod
    def _extract_or_generate_request_id(scope: MutableMapping) -> str:
        for header_name, header_val in scope.get("headers", []):
            if header_name.lower() == b"x-request-id":
                candidate = header_val.decode("latin-1").strip()
                if candidate and len(candidate) <= 128:
                    return candidate
        return uuid.uuid4().hex[:16]


def register_middlewares(app: FastAPI, settings: AppSettings) -> None:
    """Attach CORS and request-context middlewares to the FastAPI application."""
    origins = settings.cors_origins
    allow_credentials = settings.cors_allow_credentials
    if "*" in origins and allow_credentials:
        # Browsers reject wildcard origins combined with Access-Control-Allow-Credentials: true.
        allow_credentials = False

    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )

    app.add_middleware(RequestContextMiddleware)

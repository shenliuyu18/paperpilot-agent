"""Wildcard CORS turned a local install into a page-reachable attack surface.

`allow_origins=["*"]` with no authentication meant any web page a user visited could issue
`fetch("http://127.0.0.1:8765/api/…")` from the victim's browser and read their library, projects
and — together with the path bug fixed in the same release — their stored API key. The bundled
pages make no cross-origin calls (the showcase is static and the UI is served by the app itself),
so the default is now same-origin, with an explicit opt-in for people who really want to call the
API from their own front end.
"""
from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from app import config
from app.main import app


class OriginPolicyTests(unittest.TestCase):
    def test_default_is_same_origin_only(self) -> None:
        self.assertEqual(config.cors_allow_origins(), [])

    def test_configured_origins_are_kept_but_a_wildcard_is_not(self) -> None:
        """Re-enabling ``*`` would restore the exposed default for every endpoint."""
        saved = os.environ.get("PAPERPILOT_ALLOW_ORIGINS")
        try:
            os.environ["PAPERPILOT_ALLOW_ORIGINS"] = "https://my.front.example, * ,https://a.example"
            self.assertEqual(
                config.cors_allow_origins(), ["https://my.front.example", "https://a.example"])
            os.environ["PAPERPILOT_ALLOW_ORIGINS"] = "*"
            self.assertEqual(config.cors_allow_origins(), [])
        finally:
            if saved is None:
                os.environ.pop("PAPERPILOT_ALLOW_ORIGINS", None)
            else:
                os.environ["PAPERPILOT_ALLOW_ORIGINS"] = saved


class AppWiringTests(unittest.TestCase):
    def _cors_options(self) -> dict:
        for entry in app.user_middleware:
            if entry.cls is CORSMiddleware:
                return entry.kwargs
        self.fail("app 上没有 CORSMiddleware")
        return {}

    def test_the_middleware_uses_the_policy_and_never_the_wildcard(self) -> None:
        origins = self._cors_options()["allow_origins"]
        self.assertEqual(origins, config.cors_allow_origins())
        self.assertNotIn("*", origins)

    def test_a_foreign_page_gets_no_cors_header_and_the_ui_still_works(self) -> None:
        client = TestClient(app)
        denied = client.get("/api/meta", headers={"Origin": "https://evil.example"})
        self.assertEqual(denied.status_code, 200)
        self.assertIsNone(denied.headers.get("access-control-allow-origin"))
        self.assertEqual(client.get("/api/health").json()["ok"], True)
        self.assertEqual(client.get("/api/meta").json()["app"], "PaperPilot")


if __name__ == "__main__":
    unittest.main()

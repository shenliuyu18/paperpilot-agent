from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import project_service, subscription_service, subscription_state

ROOT = Path(__file__).resolve().parents[1]


class SubscriptionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "PAPERPILOT_DATA_DIR": str(root / "data"),
            "PAPERPILOT_PROJECT_FILE": str(root / "projects.json"),
            "PAPERPILOT_PROJECT_DIR": str(root / "projects"),
            "PAPERPILOT_AGENT_FILE": str(root / "runs.json"),
            "PAPERPILOT_AGENT_DIR": str(root / "runs"),
            "PAPERPILOT_INDEX_FILE": str(root / "index.json"),
            "PAPERPILOT_SUBSCRIPTIONS_FILE": str(root / "subscriptions.json"),
            "PAPERPILOT_LOCK_DIR": str(root / "locks"),
        })
        self.env.start()
        self.project = project_service.create_project("并发项目", "问题？")
        self.item = subscription_state.create({
            "label": "并发", "query": "accelerator", "source": "demo", "limit": 5,
            "project_id": self.project["id"],
        })
        self.paper = {
            "id": "P1", "title": "Paper One", "year": "2026", "source": "demo",
            "abstract": "A hardware accelerator paper.", "metrics": {}, "results": [],
        }

    def tearDown(self) -> None:
        subscription_service.stop_scheduler()
        self.env.stop()
        self.tmp.cleanup()

    def test_second_caller_is_skipped_while_the_first_is_fetching(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def fake_search(query, limit=20, source="arxiv"):
            calls.append(query)
            started.set()
            release.wait(5)
            return [dict(self.paper)]

        first_result: dict = {}
        with patch.object(subscription_service.search, "search", side_effect=fake_search):
            thread = threading.Thread(
                target=lambda: first_result.update(subscription_service.run_once(self.item["id"]))
            )
            thread.start()
            self.assertTrue(started.wait(5))
            second = subscription_service.run_once(self.item["id"])
            self.assertEqual(second["skipped"], "busy")
            self.assertEqual(subscription_service.running(), [self.item["id"]])
            self.assertIn(self.item["id"], subscription_service.scheduler_status()["running_ids"])
            release.set()
            thread.join(10)

        self.assertEqual(calls, ["accelerator"])
        self.assertEqual(first_result["result"]["new"], 1)
        self.assertEqual(subscription_service.running(), [])

    def test_due_skips_running_and_resumes_after_finishing(self) -> None:
        data = subscription_state.read()
        data["items"][0]["next_run_at"] = "2000-01-01 00:00:00"
        subscription_state.write(data)

        started = threading.Event()
        release = threading.Event()

        def fake_search(query, limit=20, source="arxiv"):
            started.set()
            release.wait(5)
            return []

        with patch.object(subscription_service.search, "search", side_effect=fake_search):
            thread = threading.Thread(target=subscription_service.run_once, args=(self.item["id"],))
            thread.start()
            self.assertTrue(started.wait(5))
            self.assertNotIn(self.item["id"], [item["id"] for item in subscription_service.due()])
            release.set()
            thread.join(10)

        # record_run moved the cursor forward, so put the due time back in the
        # past to prove the busy guard — not the interval — was what hid it.
        data = subscription_state.read()
        data["items"][0]["next_run_at"] = "2000-01-01 00:00:00"
        subscription_state.write(data)
        self.assertIn(self.item["id"], [item["id"] for item in subscription_service.due()])

    def test_lock_is_released_after_a_transport_error(self) -> None:
        def boom(query, limit=20, source="arxiv"):
            raise RuntimeError("network down")

        with patch.object(subscription_service.search, "search", side_effect=boom):
            with self.assertRaises(RuntimeError):
                subscription_service.run_once(self.item["id"])
        self.assertEqual(subscription_service.running(), [])
        # The next call is not permanently poisoned by the failure.
        with patch.object(subscription_service.search, "search", return_value=[]):
            result = subscription_service.run_once(self.item["id"])
        self.assertNotIn("skipped", result)

    def test_file_lock_blocks_another_process(self) -> None:
        code = (
            "import sys, time;"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "from app.services import subscription_service as s;"
            "token = s._acquire_file_lock(sys.argv[1]);"
            "print('locked' if token else 'busy', flush=True);"
            "time.sleep(4);"
            "s._release_file_lock(sys.argv[1], token)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code, self.item["id"]],
            cwd=str(ROOT), env=os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with patch.object(subscription_service.search, "search") as search:
                result = subscription_service.run_once(self.item["id"])
                search.assert_not_called()
            self.assertEqual(result["skipped"], "busy")
            self.assertIn(self.item["id"], subscription_service.running())
        finally:
            child.wait(timeout=15)
        self.assertNotIn(self.item["id"], subscription_service.running())

    def test_stale_lock_is_reclaimed(self) -> None:
        path = subscription_service._lock_path("sub-stale")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"pid": 1, "token": "old"}', encoding="utf-8")
        old = time.time() - subscription_service.LOCK_TTL_SECONDS - 30
        os.utime(path, (old, old))
        token = subscription_service._acquire_file_lock("sub-stale")
        self.assertTrue(token)
        subscription_service._release_file_lock("sub-stale", token)
        self.assertFalse(path.exists())

    def test_release_does_not_delete_someone_elses_lock(self) -> None:
        path = subscription_service._lock_path("sub-owned")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"pid": 1, "token": "other"}', encoding="utf-8")
        subscription_service._release_file_lock("sub-owned", "mine")
        self.assertTrue(path.exists())
        subscription_service._release_file_lock("sub-owned", "other")
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

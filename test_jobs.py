from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as app_module
from job_manager import JobManager
from pipeline_service import FullPipelineService
from upd_service import UpdContractResult
from zakupki_client import ZakupkiClient


def wait_for(manager: JobManager, job_id: str, status: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job["status"] == status:
            return job
        time.sleep(0.01)
    pytest.fail(f"Job {job_id} did not reach {status}")


def valid_form() -> dict[str, str]:
    return {
        "search_string": "Вориконазол",
        "date_from": "2025-06-01",
        "date_to": "2026-06-01",
        "records_per_page": "10",
        "drug_znvlp": "true",
        "max_contracts": "2",
    }


@pytest.fixture
def isolated_manager(monkeypatch):
    manager = JobManager()
    monkeypatch.setattr(app_module, "job_manager", manager)
    yield manager
    manager.shutdown(wait=True)


def test_post_run_creates_job_and_redirects(monkeypatch, isolated_manager):
    monkeypatch.setattr(
        app_module,
        "_execute_job",
        lambda params, maximum, filename, callback: {"result_filename": filename},
    )
    with TestClient(app_module.app) as client:
        response = client.post("/run", data=valid_form(), follow_redirects=False)
        page = client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")
    assert page.status_code == 200
    assert "Выгрузка данных ЕИС" in page.text
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    assert isolated_manager.get_job(job_id) is not None


def test_second_post_reuses_active_job(monkeypatch, isolated_manager):
    release = threading.Event()

    def blocking_job(params, maximum, filename, callback):
        callback("search_started", {})
        release.wait(2)
        return {"result_filename": filename}

    monkeypatch.setattr(app_module, "_execute_job", blocking_job)
    with TestClient(app_module.app) as client:
        first = client.post("/run", data=valid_form(), follow_redirects=False)
        second = client.post("/run", data=valid_form(), follow_redirects=False)
        release.set()

    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]


def test_progress_remaining_and_download_url():
    manager = JobManager()

    def target(callback):
        callback("search_finished", {"total": 59})
        callback("contract_started", {"index": 17, "registry_number": "123"})
        callback("contract_finished", {"index": 17})
        return {"result_filename": "result.xlsx"}

    job, _ = manager.start_job({}, target)
    finished = wait_for(manager, job["job_id"], "completed")
    manager.shutdown()

    assert finished["total_contracts"] == 59
    assert finished["processed_contracts"] == 17
    assert finished["remaining_contracts"] == 42
    assert finished["download_url"] == "/download/result.xlsx"


def test_failure_releases_single_job_lock():
    manager = JobManager()

    def fail(callback):
        raise RuntimeError("boom")

    first, _ = manager.start_job({}, fail)
    failed = wait_for(manager, first["job_id"], "error")
    second, created = manager.start_job({}, lambda callback: {})
    manager.shutdown()

    assert "RuntimeError: boom" in failed["fatal_error"]
    assert created is True
    assert second["job_id"] != first["job_id"]


def test_eis_pause_does_not_increment_processed_and_resumes():
    manager = JobManager()
    paused = threading.Event()
    release = threading.Event()

    def target(callback):
        callback("search_finished", {"total": 3})
        callback("contract_started", {"index": 1, "registry_number": "123"})
        callback(
            "pause_started",
            {"reason": "HTTP 429", "retry_after": 120},
        )
        paused.set()
        release.wait(2)
        callback("pause_finished", {})
        callback("contract_finished", {"index": 1})
        return {}

    job, _ = manager.start_job({}, target)
    assert paused.wait(1)
    state = manager.get_job(job["job_id"])
    assert state["status"] == "paused"
    assert state["processed_contracts"] == 0
    assert state["retry_after_seconds"] == 120
    release.set()
    finished = wait_for(manager, job["job_id"], "completed")
    manager.shutdown()
    assert finished["processed_contracts"] == 1


def test_nonfatal_contract_warning_still_counts_as_processed(tmp_path):
    events: list[tuple[str, dict]] = []

    class FakeClient:
        def parse_contract(self, registry_number):
            raise ValueError("card warning")

    class FakeUpdService:
        def parse_contract_upd(self, registry_number, contract_info_id=None):
            return UpdContractResult(registry_number, contract_info_id)

    pipeline = FullPipelineService(
        FakeClient(),
        output_dir=tmp_path,
        progress_callback=lambda event, data: events.append((event, data)),
    )
    pipeline.upd_service = FakeUpdService()
    result = pipeline.process_registry_numbers(["123"], delay_between_contracts=0)

    assert len(result.errors) == 1
    assert any(event == "contract_finished" for event, _ in events)


def test_http_429_pause_retries_same_request_and_continues(monkeypatch):
    events: list[str] = []

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.url = "https://zakupki.gov.ru/same"
            self.headers = {}

        def close(self):
            pass

    responses = iter([FakeResponse(429), FakeResponse(200)])
    client = ZakupkiClient(
        block_retries=1,
        min_request_interval=0,
        proactive_pause_every=0,
        pause_callback=lambda event, data: events.append(event),
    )
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return next(responses)

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr(client, "_block_delay", lambda *args: 0)
    monkeypatch.setattr("zakupki_client.time.sleep", lambda seconds: None)

    response = client._request_get("https://zakupki.gov.ru/same")
    client.close()

    assert response.status_code == 200
    assert calls == ["https://zakupki.gov.ru/same"] * 2
    assert events == ["pause_started", "pause_finished"]

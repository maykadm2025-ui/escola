import os
import threading
import time
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://epbfgvhjrcfjewwmptjj.supabase.co")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVwYmZndmhqcmNmamV3d21wdGpqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMxNzk4NjgsImV4cCI6MjA4ODc1NTg2OH0.50JJJgmtSAFCA6ivFyzFW1EiPx2hsxoF5Rl4BvOaiwQ",
)
PASSWORD = os.getenv("APP_PASSWORD", "0009")

CRON_JOB_API_KEY = os.getenv(
    "CRON_JOB_API_KEY",
    "rb2tzwwUkxr5V5LQrgQwH7V3+CjLjJRYvuKawi7kxXc=",
).strip()
CRON_API_BASE = "https://api.cron-job.org"
CRON_TIMEZONE = os.getenv("CRON_TIMEZONE", "America/Sao_Paulo")
CRON_TARGET_URL = os.getenv("CRON_TARGET_URL", "").strip()
CRON_TITLE_PREFIX = os.getenv("CRON_TITLE_PREFIX", "Escola Keep Alive")


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


CRON_SETUP_COOLDOWN_SECONDS = _int_env("CRON_SETUP_COOLDOWN_SECONDS", 10800)

_cron_state = {
    "status": "idle",
    "target_url": "",
    "job_id": None,
    "last_attempt": 0.0,
    "last_error": "",
}
_cron_lock = threading.Lock()


def _is_public_base_url(base_url):
    hostname = (urlparse(base_url).hostname or "").lower()
    return bool(hostname) and hostname not in {"localhost", "127.0.0.1"} and not hostname.endswith(".local")


def _resolve_cron_target(base_url):
    if CRON_TARGET_URL:
        return CRON_TARGET_URL
    if not _is_public_base_url(base_url):
        return ""
    normalized = base_url if base_url.endswith("/") else f"{base_url}/"
    return urljoin(normalized, "cron/ping")


def _cron_title(target_url):
    host = urlparse(target_url).netloc or "app"
    return f"{CRON_TITLE_PREFIX} - {host}"


def _cron_schedule(hours, minutes):
    return {
        "timezone": CRON_TIMEZONE,
        "expiresAt": 0,
        "hours": hours,
        "mdays": [-1],
        "minutes": minutes,
        "months": [-1],
        "wdays": [1, 2, 3, 4, 5],
    }


def _cron_job_definitions(target_url):
    base_title = _cron_title(target_url)
    return [
        {
            "title": f"{base_title} - seg-sex 07h-21h59",
            "schedule": _cron_schedule(list(range(7, 22)), [-1]),
        },
        {
            "title": f"{base_title} - seg-sex 22h00",
            "schedule": _cron_schedule([22], [0]),
        },
    ]


def _normalize_job_url(url):
    parsed = urlparse((url or "").strip())
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    return scheme, host, path


def _is_legacy_root_job(job, target_url):
    target_scheme, target_host, _ = _normalize_job_url(target_url)
    job_scheme, job_host, job_path = _normalize_job_url(job.get("url"))
    if job_host != target_host:
        return False
    if job_scheme != target_scheme:
        return False
    return job_path in {"", "/"}


def _is_same_site_job(job, target_url):
    target_scheme, target_host, _ = _normalize_job_url(target_url)
    job_scheme, job_host, _ = _normalize_job_url(job.get("url"))
    return job_host == target_host and job_scheme == target_scheme


def _is_managed_job(job, target_url):
    if job.get("url") == target_url:
        return True
    if _is_legacy_root_job(job, target_url):
        return True
    title = (job.get("title") or "").strip()
    return _is_same_site_job(job, target_url) and title.startswith(CRON_TITLE_PREFIX)


def _collect_managed_jobs(jobs, target_url):
    return [job for job in jobs if _is_managed_job(job, target_url)]


def _select_job_for_definition(jobs, target_url, definition):
    title = definition["title"]
    for job in jobs:
        if job.get("url") == target_url and job.get("title") == title:
            return job
    for job in jobs:
        if job.get("title") == title:
            return job
    for job in jobs:
        if job.get("url") == target_url:
            return job
    for job in jobs:
        if _is_legacy_root_job(job, target_url):
            return job
    return jobs[0] if jobs else None


def _cron_payload(target_url, definition):
    return {
        "job": {
            "enabled": True,
            "title": definition["title"],
            "url": target_url,
            "saveResponses": False,
            "requestMethod": 3,
            "requestTimeout": 30,
            "redirectSuccess": False,
            "schedule": definition["schedule"],
        }
    }


def _cron_headers():
    return {
        "Authorization": f"Bearer {CRON_JOB_API_KEY}",
        "Content-Type": "application/json",
    }


def _format_request_error(exc):
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    body = (response.text or "").strip()
    if body:
        return f"{response.status_code}: {body[:300]}"
    return f"{response.status_code}: {response.reason}"


def _update_cron_state(**kwargs):
    with _cron_lock:
        _cron_state.update(kwargs)


def _configure_cron_job(base_url):
    target_url = _resolve_cron_target(base_url)
    if not CRON_JOB_API_KEY or not target_url:
        _update_cron_state(status="skipped", target_url=target_url, job_id=None, last_error="")
        return

    try:
        list_response = requests.get(
            f"{CRON_API_BASE}/jobs",
            headers=_cron_headers(),
            timeout=15,
        )
        list_response.raise_for_status()
        jobs = list_response.json().get("jobs", [])
        managed_jobs = _collect_managed_jobs(jobs, target_url)
        remaining_jobs = managed_jobs[:]
        configured_job_ids = []
        configured_count = 0

        for definition in _cron_job_definitions(target_url):
            existing_job = _select_job_for_definition(remaining_jobs, target_url, definition)
            payload = _cron_payload(target_url, definition)

            if existing_job:
                job_id = existing_job["jobId"]
                save_response = requests.patch(
                    f"{CRON_API_BASE}/jobs/{job_id}",
                    headers=_cron_headers(),
                    json=payload,
                    timeout=15,
                )
                save_response.raise_for_status()
                remaining_jobs = [job for job in remaining_jobs if job.get("jobId") != job_id]
            else:
                create_response = requests.put(
                    f"{CRON_API_BASE}/jobs",
                    headers=_cron_headers(),
                    json=payload,
                    timeout=15,
                )
                create_response.raise_for_status()
                job_id = create_response.json().get("jobId")

            configured_job_ids.append(job_id)
            configured_count += 1

        for job in remaining_jobs:
            duplicate_job_id = job.get("jobId")
            if duplicate_job_id in configured_job_ids:
                continue
            disable_response = requests.patch(
                f"{CRON_API_BASE}/jobs/{duplicate_job_id}",
                headers=_cron_headers(),
                json={"job": {"enabled": False}},
                timeout=15,
            )
            disable_response.raise_for_status()

        _update_cron_state(
            status="updated",
            target_url=target_url,
            job_id=configured_job_ids[0] if configured_job_ids else None,
            last_error="",
        )
        app.logger.info(
            "cron-job.org configured %s jobs for %s (job_ids=%s)",
            configured_count,
            target_url,
            configured_job_ids,
        )
    except requests.RequestException as exc:
        error_message = _format_request_error(exc)
        _update_cron_state(status="error", target_url=target_url, job_id=None, last_error=error_message)
        app.logger.warning("Falha ao configurar cron-job.org para %s: %s", target_url, error_message)


def maybe_setup_cron(base_url):
    if not CRON_JOB_API_KEY:
        return

    target_url = _resolve_cron_target(base_url)
    if not target_url:
        return

    now = time.time()
    with _cron_lock:
        same_target = _cron_state["target_url"] == target_url
        is_recent = same_target and (now - _cron_state["last_attempt"] < CRON_SETUP_COOLDOWN_SECONDS)
        if _cron_state["status"] == "running" or is_recent:
            return

        _cron_state.update(
            {
                "status": "running",
                "target_url": target_url,
                "job_id": _cron_state.get("job_id"),
                "last_attempt": now,
                "last_error": "",
            }
        )

    threading.Thread(target=_configure_cron_job, args=(base_url,), daemon=True).start()


@app.route("/")
def index():
    maybe_setup_cron(request.url_root)
    return render_template(
        "index.html",
        supabase_url=SUPABASE_URL,
        supabase_key=SUPABASE_KEY,
        password=PASSWORD,
    )


@app.route("/cron/ping", methods=["GET", "HEAD"])
def cron_ping():
    return ("", 204, {"Cache-Control": "no-store, max-age=0"})


if __name__ == "__main__":
    app.run(debug=True)

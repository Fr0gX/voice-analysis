"""Long-running acceptance load for the batch PCM embedding service.

Example release gate (supply twice the measured pre-refactor throughput):

    python -m voice_embedding_service.scripts.soak_test \
      --duration-hours 8 --concurrency 4 --min-windows-per-second 12
"""
from __future__ import annotations

import argparse
from array import array
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid


def _fixture() -> tuple[bytes, list[dict[str, object]]]:
    # 52 four-second + 10 three-second windows = today's 62-window/238-second shape.
    one_second = array(
        "h",
        (int(5_000 * math.sin(2 * math.pi * 220 * i / 16_000)) for i in range(16_000)),
    ).tobytes()
    pcm = bytearray()
    windows: list[dict[str, object]] = []
    for index, seconds in enumerate([4] * 52 + [3] * 10):
        raw = one_second * seconds
        offset = len(pcm)
        pcm.extend(raw)
        windows.append({
            "window_id": f"window:{index}",
            "offset": offset,
            "length": len(raw),
            "kind": "sentence" if index % 2 else "gold",
        })
    return bytes(pcm), windows


def _multipart(metadata: dict[str, object], pcm: bytes) -> tuple[bytes, str]:
    boundary = "----voice-analysis-" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n"
        + json.dumps(metadata, separators=(",", ":"))
        + f"\r\n--{boundary}\r\n"
        + "Content-Disposition: form-data; name=\"audio\"; filename=\"windows.pcm\"\r\n"
        + "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return head + pcm + f"\r\n--{boundary}--\r\n".encode(), boundary


def _health(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embed-url", default="http://127.0.0.1:8077/embed")
    parser.add_argument("--health-url", default="http://127.0.0.1:8077/health")
    parser.add_argument("--duration-hours", type=float, default=8.0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--min-windows-per-second", type=float, default=0.0)
    parser.add_argument("--allow-overload", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("VOICEANALYSIS_API_KEY", "")
    auth_header = os.getenv("VOICEANALYSIS_AUTH_HEADER", "X-Voice-Analysis-Key")
    if not api_key:
        raise SystemExit("VOICEANALYSIS_API_KEY is missing")

    initial = _health(args.health_url)
    model_version = str(initial.get("model_version") or "")
    if not initial.get("model_ready") or not model_version:
        raise SystemExit("embedding model is not ready")
    pcm, windows = _fixture()
    started = time.monotonic()
    stop_at = started + args.duration_hours * 3600
    lock = threading.Lock()
    latencies: list[float] = []
    errors: list[str] = []
    successes = 0
    overloads = 0
    rss_samples: list[tuple[float, int]] = []
    max_threads = 0

    def worker(worker_id: int) -> None:
        nonlocal successes, overloads
        while time.monotonic() < stop_at:
            request_id = f"soak:{worker_id}:{uuid.uuid4().hex}"
            metadata = {
                "request_id": request_id,
                "expected_model_version": model_version,
                "deadline_ms": int(time.time() * 1000) + 120_000,
                "sample_rate": 16_000,
                "windows": windows,
            }
            body, boundary = _multipart(metadata, pcm)
            request = urllib.request.Request(
                args.embed_url,
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    auth_header: api_key,
                },
                method="POST",
            )
            call_started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=125) as response:
                    payload = json.loads(response.read())
                statuses = [item.get("status") for item in payload.get("items", [])]
                if len(statuses) != len(windows) or any(status != "success" for status in statuses):
                    raise RuntimeError("response contains missing or failed windows")
                with lock:
                    successes += 1
                    latencies.append(time.monotonic() - call_started)
            except urllib.error.HTTPError as exc:
                with lock:
                    if exc.code == 429:
                        overloads += 1
                    else:
                        errors.append(f"HTTP {exc.code}")
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")

    def monitor() -> None:
        nonlocal max_threads
        while time.monotonic() < stop_at:
            try:
                runtime = _health(args.health_url).get("runtime") or {}
                rss = runtime.get("rss_bytes")
                threads = runtime.get("native_threads")
                with lock:
                    if isinstance(rss, int):
                        rss_samples.append((time.monotonic(), rss))
                    if isinstance(threads, int):
                        max_threads = max(max_threads, threads)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"health: {type(exc).__name__}: {exc}")
            time.sleep(min(60.0, max(1.0, stop_at - time.monotonic())))

    monitor_thread = threading.Thread(target=monitor, name="soak-health", daemon=True)
    monitor_thread.start()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(worker, index) for index in range(args.concurrency)]
        for future in futures:
            future.result()
    monitor_thread.join(timeout=65)

    elapsed = time.monotonic() - started
    throughput = successes * len(windows) / elapsed if elapsed else 0.0
    max_rss = max((rss for _, rss in rss_samples), default=0)
    growth_per_hour = 0.0
    if len(rss_samples) >= 2 and rss_samples[-1][0] > rss_samples[0][0]:
        growth_per_hour = (
            (rss_samples[-1][1] - rss_samples[0][1])
            / (rss_samples[-1][0] - rss_samples[0][0])
            * 3600
        )
    report = {
        "elapsed_seconds": round(elapsed, 2),
        "requests_succeeded": successes,
        "overloads": overloads,
        "errors": errors[:20],
        "windows_per_second": round(throughput, 3),
        "latency_p50_seconds": round(statistics.median(latencies), 3) if latencies else None,
        "latency_max_seconds": round(max(latencies), 3) if latencies else None,
        "max_rss_bytes": max_rss or None,
        "rss_growth_bytes_per_hour": round(growth_per_hour),
        "max_native_threads": max_threads or None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failures = list(errors)
    if not successes:
        failures.append("no successful request")
    if overloads and not args.allow_overload:
        failures.append(f"unexpected overloads: {overloads}")
    if max_rss and max_rss >= 4 * 1024**3:
        failures.append("RSS reached 4 GiB")
    if args.duration_hours >= 1 and growth_per_hour >= 50 * 1024**2:
        failures.append("RSS growth reached 50 MiB/hour")
    if max_threads > 64:
        failures.append("native thread count exceeded 64")
    if throughput < args.min_windows_per_second:
        failures.append("window throughput below configured gate")
    if failures:
        raise SystemExit("; ".join(failures[:20]))


if __name__ == "__main__":
    main()

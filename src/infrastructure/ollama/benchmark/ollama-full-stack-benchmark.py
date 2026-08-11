#!/usr/bin/env python3
"""TESI2 full Ollama stack benchmark.

Models:
- gemma4:e2b for reasoning (ctx 8192)
- qwen3:4b-instruct for tool selection (ctx 4096)
- ibm/granite-embedding:30m for RAG embeddings

A global application-level semaphore allows at most 2 Ollama calls at once.
Additional calls wait in a software queue.

No third-party Python packages are required.
"""
from __future__ import annotations

import concurrent.futures
import csv
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

OLLAMA = "http://127.0.0.1:11434"
GENERATE_URL = f"{OLLAMA}/api/generate"
CHAT_URL = f"{OLLAMA}/api/chat"
EMBED_URL = f"{OLLAMA}/api/embed"
TAGS_URL = f"{OLLAMA}/api/tags"

REASONING_MODEL = "gemma4:e2b"
TOOL_MODEL = "qwen3:4b-instruct"
EMBEDDING_MODEL = "ibm/granite-embedding:30m"

REASONING_CONTEXT = 8192
TOOL_CONTEXT = 4096
MAX_OLLAMA_CONCURRENCY = 2
SIMULATED_AGENTS = 4
REASON_OUTPUT_TOKENS = 192
TIMEOUT = 600

REASON_PROMPT = """You are the reasoning component of an autonomous IT monitoring agent.

Current BDI state:
Beliefs:
- processing-service has abnormal CPU usage
- memory usage appears normal
- network latency appears normal
- the root cause is not yet known

Current intention:
Determine the next evidence needed to discriminate among plausible root causes.

Reason from the evidence already available. State the next diagnostic information
that should be collected and why. Do not invent observations.
"""

POST_OBSERVATION_PROMPT = """You are the reasoning component of an autonomous IT monitoring agent.

Current BDI state:
Beliefs:
- processing-service CPU usage = 394 percent
- memory usage is normal
- network latency is normal
- retrieved knowledge says processing-service contains CPU-bound processing logic

Current intention:
Refine the diagnosis after receiving new evidence.

Explain what the evidence supports, what remains uncertain, and what diagnostic
step should be performed next. Do not invent observations.
"""

RAG_QUERY = (
    "processing-service high CPU saturation diagnosis normal memory "
    "normal network latency CPU-bound application troubleshooting"
)

TOOL_PROMPT = """You are the tool-selection component of an autonomous monitoring agent.

The reasoning component decided that the current CPU metric for processing-service
must be retrieved before the diagnosis can continue.

Use the appropriate available tool. Do not invent the metric value.
"""

GET_METRICS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_metrics",
        "description": "Retrieve a metric for a monitored service.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "metric": {"type": "string"},
            },
            "required": ["service", "metric"],
        },
    },
}


@dataclass
class CallMetric:
    scenario: str
    agent: str
    phase: str
    model: str
    queue_wait_s: float
    latency_s: float
    total_duration_s: float
    load_duration_s: float
    prompt_tokens: int
    output_tokens: int
    eval_tps: float
    embedding_dimension: int = 0
    tool_ok: bool | None = None
    error: str = ""


class GPUMonitor:
    def __init__(self, interval_s: float = 0.20):
        self.interval_s = interval_s
        self.peak_vram_mib = 0.0
        self.peak_gpu_util_pct = 0.0
        self.ps_snapshots: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_ps = 0.0

    def _poll(self):
        while not self._stop.is_set():
            try:
                gpu = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if gpu.returncode == 0 and gpu.stdout.strip():
                    line = gpu.stdout.strip().splitlines()[0]
                    memory, util = [x.strip() for x in line.split(",")[:2]]
                    self.peak_vram_mib = max(self.peak_vram_mib, float(memory))
                    self.peak_gpu_util_pct = max(self.peak_gpu_util_pct, float(util))

                now = time.perf_counter()
                if now - self._last_ps >= 0.75:
                    self._last_ps = now
                    ps = subprocess.run(
                        ["ollama", "ps"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    text = ps.stdout.strip()
                    if text and (not self.ps_snapshots or self.ps_snapshots[-1] != text):
                        self.ps_snapshots.append(text)
            except Exception:
                pass
            self._stop.wait(self.interval_s)

    def __enter__(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: int = TIMEOUT) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_ollama():
    try:
        http_json(TAGS_URL, timeout=10)
    except Exception as exc:
        print("ERROR: Ollama is not reachable at http://127.0.0.1:11434")
        print(exc)
        sys.exit(1)


def installed_models() -> set[str]:
    response = http_json(TAGS_URL)
    result = set()
    for item in response.get("models", []):
        if item.get("name"):
            result.add(item["name"])
        if item.get("model"):
            result.add(item["model"])
    return result


def verify_models():
    available = installed_models()
    required = [REASONING_MODEL, TOOL_MODEL, EMBEDDING_MODEL]
    missing = [m for m in required if m not in available]
    if missing:
        print("Missing model(s):")
        for model in missing:
            print(f"  - {model}")
        print("\nInstall them first:")
        for model in missing:
            print(f"  ollama pull {model}")
        sys.exit(1)


def print_ollama_ps():
    result = subprocess.run(
        ["ollama", "ps"], capture_output=True, text=True, timeout=10, check=False
    )
    print(result.stdout.strip() or "(no models loaded)")


def normalize_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def tool_call_is_correct(response: dict[str, Any]) -> bool:
    calls = (response.get("message") or {}).get("tool_calls") or []
    for call in calls:
        function = call.get("function") or {}
        if function.get("name") != "get_metrics":
            continue
        arguments = normalize_tool_arguments(function.get("arguments"))
        service = str(arguments.get("service", "")).lower().strip()
        metric = str(arguments.get("metric", "")).lower().strip()
        if service == "processing-service" and metric in {
            "cpu", "cpu_usage", "cpu usage", "cpu_usage_percent"
        }:
            return True
    return False


def raw_reason(prompt: str, tag: str) -> tuple[dict[str, Any], float]:
    payload = {
        "model": REASONING_MODEL,
        "prompt": prompt + f"\nBenchmark tag: {tag}",
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "num_ctx": REASONING_CONTEXT,
            "num_predict": REASON_OUTPUT_TOKENS,
            "temperature": 0,
        },
    }
    start = time.perf_counter()
    response = http_json(GENERATE_URL, payload)
    return response, time.perf_counter() - start


def raw_tool(tag: str) -> tuple[dict[str, Any], float]:
    payload = {
        "model": TOOL_MODEL,
        "messages": [{"role": "user", "content": TOOL_PROMPT + f"\nBenchmark tag: {tag}"}],
        "tools": [GET_METRICS_TOOL],
        "stream": False,
        "keep_alive": "10m",
        "options": {"num_ctx": TOOL_CONTEXT, "temperature": 0},
    }
    start = time.perf_counter()
    response = http_json(CHAT_URL, payload)
    return response, time.perf_counter() - start


def raw_embed(text: str) -> tuple[dict[str, Any], float]:
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text,
        "keep_alive": "10m",
    }
    start = time.perf_counter()
    response = http_json(EMBED_URL, payload)
    return response, time.perf_counter() - start


class OllamaGate:
    """Global application-level resource gate: max two Ollama calls at once."""
    def __init__(self, capacity: int):
        self._semaphore = threading.Semaphore(capacity)

    def run(self, fn: Callable[[], tuple[dict[str, Any], float]]):
        queued_at = time.perf_counter()
        self._semaphore.acquire()
        acquired_at = time.perf_counter()
        try:
            response, latency = fn()
            return response, latency, acquired_at - queued_at
        finally:
            self._semaphore.release()


def make_metric(
    scenario: str,
    agent: str,
    phase: str,
    model: str,
    queue_wait_s: float,
    latency_s: float,
    response: dict[str, Any],
    *,
    tool_ok: bool | None = None,
    embedding_dimension: int = 0,
) -> CallMetric:
    eval_count = int(response.get("eval_count") or 0)
    eval_duration_s = float(response.get("eval_duration") or 0) / 1e9
    return CallMetric(
        scenario=scenario,
        agent=agent,
        phase=phase,
        model=model,
        queue_wait_s=queue_wait_s,
        latency_s=latency_s,
        total_duration_s=float(response.get("total_duration") or 0) / 1e9,
        load_duration_s=float(response.get("load_duration") or 0) / 1e9,
        prompt_tokens=int(response.get("prompt_eval_count") or 0),
        output_tokens=eval_count,
        eval_tps=(eval_count / eval_duration_s if eval_duration_s > 0 else 0.0),
        embedding_dimension=embedding_dimension,
        tool_ok=tool_ok,
    )


def gated_reason(gate: OllamaGate, scenario: str, agent: str, phase: str, prompt: str, tag: str) -> CallMetric:
    try:
        response, latency, wait = gate.run(lambda: raw_reason(prompt, tag))
        return make_metric(scenario, agent, phase, REASONING_MODEL, wait, latency, response)
    except Exception as exc:
        return CallMetric(scenario, agent, phase, REASONING_MODEL, 0, 0, 0, 0, 0, 0, 0, error=f"{type(exc).__name__}: {exc}")


def gated_tool(gate: OllamaGate, scenario: str, agent: str, tag: str) -> CallMetric:
    try:
        response, latency, wait = gate.run(lambda: raw_tool(tag))
        return make_metric(
            scenario, agent, "tool_select", TOOL_MODEL, wait, latency, response,
            tool_ok=tool_call_is_correct(response),
        )
    except Exception as exc:
        return CallMetric(scenario, agent, "tool_select", TOOL_MODEL, 0, 0, 0, 0, 0, 0, 0, tool_ok=False, error=f"{type(exc).__name__}: {exc}")


def gated_embed(gate: OllamaGate, scenario: str, agent: str, tag: str) -> CallMetric:
    try:
        response, latency, wait = gate.run(lambda: raw_embed(RAG_QUERY + f"\nBenchmark tag: {tag}"))
        vectors = response.get("embeddings") or []
        dim = len(vectors[0]) if vectors and isinstance(vectors[0], list) else 0
        return make_metric(
            scenario, agent, "embedding", EMBEDDING_MODEL, wait, latency, response,
            embedding_dimension=dim,
        )
    except Exception as exc:
        return CallMetric(scenario, agent, "embedding", EMBEDDING_MODEL, 0, 0, 0, 0, 0, 0, 0, embedding_dimension=0, error=f"{type(exc).__name__}: {exc}")


def test_embedding_baseline():
    gate = OllamaGate(MAX_OLLAMA_CONCURRENCY)
    start = time.perf_counter()
    metrics = [gated_embed(gate, "embedding_baseline", "rag-client", f"emb-{i}") for i in range(5)]
    return metrics, time.perf_counter() - start


def test_three_requests_two_slots():
    gate = OllamaGate(MAX_OLLAMA_CONCURRENCY)
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(gated_reason, gate, "three_requests_two_slots", "reason-agent", "reason", REASON_PROMPT, "triple-reason"),
            pool.submit(gated_tool, gate, "three_requests_two_slots", "tool-agent", "triple-tool"),
            pool.submit(gated_embed, gate, "three_requests_two_slots", "rag-agent", "triple-embed"),
        ]
        metrics = [f.result() for f in futures]
    return metrics, time.perf_counter() - start


def one_agent_workflow(agent_id: int, gate: OllamaGate) -> list[CallMetric]:
    agent = f"agent-{agent_id}"
    scenario = "full_agentic_workload"
    before = gated_reason(gate, scenario, agent, "reason_before_rag", REASON_PROMPT, f"{agent}-reason-before")
    embedding = gated_embed(gate, scenario, agent, f"{agent}-rag")
    tool = gated_tool(gate, scenario, agent, f"{agent}-tool")
    after = gated_reason(gate, scenario, agent, "reason_after_observation", POST_OBSERVATION_PROMPT, f"{agent}-reason-after")
    return [before, embedding, tool, after]


def test_full_agentic_workload():
    gate = OllamaGate(MAX_OLLAMA_CONCURRENCY)
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=SIMULATED_AGENTS) as pool:
        futures = [pool.submit(one_agent_workflow, i, gate) for i in range(1, SIMULATED_AGENTS + 1)]
        metrics = [metric for future in futures for metric in future.result()]
    return metrics, time.perf_counter() - start


def print_scenario(title: str, metrics: list[CallMetric], wall_s: float, gpu: GPUMonitor):
    print("\n" + "=" * 110)
    print(title)
    print("=" * 110)
    for m in sorted(metrics, key=lambda x: (x.agent, x.phase)):
        extra = ""
        if m.tool_ok is not None:
            extra += f" tool_ok={m.tool_ok}"
        if m.embedding_dimension:
            extra += f" dim={m.embedding_dimension}"
        if m.error:
            extra += f" ERROR={m.error}"
        print(
            f"{m.agent:12} {m.phase:26} {m.model:30} "
            f"queue={m.queue_wait_s:6.2f}s lat={m.latency_s:6.2f}s "
            f"load={m.load_duration_s:6.2f}s eval={m.eval_tps:7.2f}{extra}"
        )
    print(f"\nWall time:       {wall_s:.2f}s")
    print(f"Peak VRAM:       {gpu.peak_vram_mib:.0f} MiB")
    print(f"Peak GPU util:   {gpu.peak_gpu_util_pct:.0f}%")
    waits = [m.queue_wait_s for m in metrics]
    loads = [m.load_duration_s for m in metrics]
    if waits:
        print(f"Avg queue wait:  {statistics.mean(waits):.2f}s")
        print(f"Max queue wait:  {max(waits):.2f}s")
    if loads:
        print(f"Avg load time:   {statistics.mean(loads):.2f}s")
        print(f"Max load time:   {max(loads):.2f}s")
    embeds = [m for m in metrics if m.phase == "embedding" and not m.error]
    if embeds:
        print(f"Embedding avg latency: {statistics.mean(m.latency_s for m in embeds):.3f}s")
        print("Embedding dimension(s): " + ", ".join(str(x) for x in sorted({m.embedding_dimension for m in embeds})))
    tools = [m for m in metrics if m.tool_ok is not None]
    if tools:
        print(f"Tool calls OK:   {sum(1 for m in tools if m.tool_ok)}/{len(tools)}")
    if gpu.ps_snapshots:
        print("\nObserved ollama ps states:")
        for snapshot in gpu.ps_snapshots[-5:]:
            print("---")
            print(snapshot)


def windows_log_path() -> Path | None:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        return None
    path = Path(root) / "Ollama" / "server.log"
    return path if path.exists() else None


def read_log_after(path: Path | None, offset: int) -> str:
    if not path:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read()
    except Exception:
        return ""


def save_results(all_metrics: list[CallMetric], scenarios: dict[str, Any], relevant_logs: list[str]):
    out = Path("../../../../../../../ollama_full_stack_benchmark_results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out / f"full_stack_calls_{stamp}.csv"
    json_path = out / f"full_stack_summary_{stamp}.json"
    rows = [asdict(m) for m in all_metrics]
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    json_path.write_text(
        json.dumps(
            {
                "configuration": {
                    "reasoning_model": REASONING_MODEL,
                    "tool_model": TOOL_MODEL,
                    "embedding_model": EMBEDDING_MODEL,
                    "reasoning_context": REASONING_CONTEXT,
                    "tool_context": TOOL_CONTEXT,
                    "max_ollama_concurrency": MAX_OLLAMA_CONCURRENCY,
                    "simulated_agents": SIMULATED_AGENTS,
                },
                "scenarios": scenarios,
                "calls": rows,
                "relevant_ollama_log_lines": relevant_logs,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return csv_path, json_path


def main():
    print("TESI2 - FULL OLLAMA STACK BENCHMARK")
    print("------------------------------------")
    print(f"Reasoning:           {REASONING_MODEL}")
    print(f"Reasoning context:   {REASONING_CONTEXT}")
    print(f"Tool model:          {TOOL_MODEL}")
    print(f"Tool context:        {TOOL_CONTEXT}")
    print(f"Embedding:           {EMBEDDING_MODEL}")
    print(f"Global Ollama slots: {MAX_OLLAMA_CONCURRENCY}")
    print(f"Simulated agents:    {SIMULATED_AGENTS}\n")

    check_ollama()
    verify_models()

    log_path = windows_log_path()
    log_offset = log_path.stat().st_size if log_path else 0

    print("Initial ollama ps:")
    print_ollama_ps()

    print("\nWarm-up Gemma...")
    r, elapsed = raw_reason(REASON_PROMPT, "warmup-gemma")
    print(f"Gemma: {elapsed:.2f}s | load={float(r.get('load_duration') or 0)/1e9:.2f}s")
    print_ollama_ps()

    print("\nWarm-up Qwen...")
    r, elapsed = raw_tool("warmup-qwen")
    print(f"Qwen: {elapsed:.2f}s | load={float(r.get('load_duration') or 0)/1e9:.2f}s | tool_ok={tool_call_is_correct(r)}")
    print_ollama_ps()

    print("\nWarm-up Granite embedding...")
    r, elapsed = raw_embed(RAG_QUERY)
    vectors = r.get("embeddings") or []
    dim = len(vectors[0]) if vectors and isinstance(vectors[0], list) else 0
    print(f"Granite: {elapsed:.3f}s | load={float(r.get('load_duration') or 0)/1e9:.3f}s | dimension={dim}")
    print_ollama_ps()

    all_metrics: list[CallMetric] = []
    scenarios: dict[str, Any] = {}

    with GPUMonitor() as gpu:
        metrics, wall = test_embedding_baseline()
    all_metrics += metrics
    scenarios["embedding_baseline"] = {"wall_s": wall, "peak_vram_mib": gpu.peak_vram_mib, "peak_gpu_util_pct": gpu.peak_gpu_util_pct}
    print_scenario("TEST 1 - GRANITE EMBEDDING BASELINE (5 REQUESTS)", metrics, wall, gpu)

    with GPUMonitor() as gpu:
        metrics, wall = test_three_requests_two_slots()
    all_metrics += metrics
    scenarios["three_requests_two_slots"] = {"wall_s": wall, "peak_vram_mib": gpu.peak_vram_mib, "peak_gpu_util_pct": gpu.peak_gpu_util_pct}
    print_scenario("TEST 2 - GEMMA + QWEN + GRANITE SUBMITTED TOGETHER, GLOBAL LIMIT = 2", metrics, wall, gpu)

    with GPUMonitor() as gpu:
        metrics, wall = test_full_agentic_workload()
    all_metrics += metrics
    scenarios["full_agentic_workload"] = {
        "wall_s": wall,
        "peak_vram_mib": gpu.peak_vram_mib,
        "peak_gpu_util_pct": gpu.peak_gpu_util_pct,
        "simulated_agents": SIMULATED_AGENTS,
        "max_ollama_concurrency": MAX_OLLAMA_CONCURRENCY,
    }
    print_scenario("TEST 3 - 4 AGENTS: GEMMA -> GRANITE/RAG -> QWEN TOOL -> GEMMA, GLOBAL LIMIT = 2", metrics, wall, gpu)

    new_log = read_log_after(log_path, log_offset)
    relevant_logs = [
        line.strip() for line in new_log.splitlines()
        if (
            "parallel requests" in line.lower()
            or "insufficient" in line.lower()
            or "out of memory" in line.lower()
            or (("memory" in line.lower()) and ("warn" in line.lower() or "error" in line.lower()))
        )
    ]

    print("\n" + "=" * 110)
    print("OLLAMA WARNINGS GENERATED DURING THE TEST")
    print("=" * 110)
    if relevant_logs:
        for line in relevant_logs[-30:]:
            print(line)
    else:
        print("No relevant parallel/memory/OOM warnings detected.")

    print("\n" + "#" * 110)
    print("AUTOMATIC INTERPRETATION")
    print("#" * 110)

    full = [m for m in all_metrics if m.scenario == "full_agentic_workload" and not m.error]
    if full:
        peak = scenarios["full_agentic_workload"]["peak_vram_mib"]
        waits = [m.queue_wait_s for m in full]
        loads = [m.load_duration_s for m in full]
        embeds = [m for m in full if m.phase == "embedding"]
        tools = [m for m in full if m.tool_ok is not None]

        print(f"Full workload peak VRAM: {peak:.0f} MiB / 8188 MiB ({peak/8188*100:.1f}%)")
        print(f"Average application queue wait: {statistics.mean(waits):.2f}s")
        print(f"Maximum application queue wait: {max(waits):.2f}s")
        print(f"Maximum model load_duration: {max(loads):.2f}s")
        if embeds:
            print(f"Granite average embedding latency: {statistics.mean(m.latency_s for m in embeds):.3f}s")
            print(f"Granite embedding dimension: {embeds[0].embedding_dimension}")
        if tools:
            print(f"Qwen tool correctness: {sum(1 for m in tools if m.tool_ok)}/{len(tools)}")

        if peak < 7000:
            print("VRAM verdict: GOOD HEADROOM.")
        elif peak < 7800:
            print("VRAM verdict: ACCEPTABLE; keep global concurrency at 2.")
        else:
            print("VRAM verdict: VERY CLOSE TO THE 8 GB LIMIT; create more headroom before production-like tests.")

        if max(loads) <= 1.0:
            print("Model residency verdict: no strong model-swapping penalty detected.")
        else:
            print("Model residency verdict: model loading/swapping is visible and should be reviewed.")

        if relevant_logs:
            print("Ollama log verdict: warnings detected; inspect them above.")
        else:
            print("Ollama log verdict: no relevant memory/parallel warnings.")

    csv_path, json_path = save_results(all_metrics, scenarios, relevant_logs)
    print("\nResults saved to:")
    print(f"CSV:  {csv_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")


if __name__ == "__main__":
    main()

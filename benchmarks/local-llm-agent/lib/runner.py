#!/usr/bin/env python3
"""local-llm-agent benchmark runner — pack ei-agent (issue #22).

Runs ONE arm of the 4-arm CLI-agent benchmark over the T1/T2/T3 task suite and
appends one JSONL record per (arm, model, task) to runs/.

Arms (same suite, same prompts, default CLI configs, no per-arm tuning):
  1  Claude Code  + Claude (host credentials; baseline)
  2  Claude Code  + Mistral via local LiteLLM /v1/messages (env-only rewiring)
  3  Mistral Vibe + Mistral direct
  4  OpenCode     + Mistral direct (OpenAI-compatible)

Secrets: MISTRAL_API_KEY must arrive via the environment (sanctioned seam:
`bin/secret no-prod/mistral.enc.env MISTRAL_API_KEY` in vexa-secrets). It is only
ever passed to containers via `docker run -e`; never written to disk or logs.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # benchmarks/local-llm-agent
FIXTURES = HERE / "fixtures"
PROMPTS = HERE / "prompts"
RUNS = HERE / "runs"
WORK = HERE / ".work"

sys.path.insert(0, str(HERE / "lib"))
import score as scoring  # noqa: E402

TASKS = ["t1", "t2", "t3"]
ARM_CLI = {1: "claude-code", 2: "claude-code+litellm", 3: "vibe", 4: "opencode"}
IMAGES = {1: "vexa-bench-claude", 2: "vexa-bench-claude", 3: "vexa-bench-vibe", 4: "vexa-bench-opencode"}
TASK_TIMEOUT_S = int(os.environ.get("TASK_TIMEOUT_S", "600"))
MAX_TURNS = os.environ.get("MAX_TURNS", "40")


def log(msg: str) -> None:
    print(f"  [runner] {msg}", flush=True)


def prepare_workdir(task: str, run_id: str) -> Path:
    wd = WORK / run_id / task
    wd.mkdir(parents=True, exist_ok=True)
    if task == "t1":
        shutil.copytree(FIXTURES / "transcripts", wd / "transcripts", dirs_exist_ok=True)
        (wd / "schema").mkdir(exist_ok=True)
        shutil.copy(FIXTURES / "schema" / "meeting-note.schema.md", wd / "schema" / "meeting-note.schema.md")
    elif task == "t2":
        shutil.copytree(FIXTURES / "workspace-t2" / "notes", wd / "notes", dirs_exist_ok=True)
        qs = json.loads((FIXTURES / "questions" / "t2-questions.json").read_text())["questions"]
        lines = ["# Questions", ""]
        for q in qs:
            lines += [f"## {q['id']}", q["question"], ""]
        (wd / "questions.md").write_text("\n".join(lines))
    elif task == "t3":
        (wd / "data").mkdir(exist_ok=True)
        shutil.copy(FIXTURES / "t3" / "services.txt", wd / "data" / "services.txt")
    # world-writable so the container's non-root `agent` user (uid 1001) can write
    for p in [wd, *wd.rglob("*")]:
        p.chmod(0o777 if p.is_dir() else 0o666)
    return wd


def run_cmd(cmd, timeout, env_extra=None):
    """Run a command; return (exit_code, stdout, stderr, wall_clock_s). Never raises."""
    env = {**os.environ, **(env_extra or {})}
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr, time.monotonic() - t0
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return 124, out, err + "\n[runner] TIMEOUT", time.monotonic() - t0


def docker_base(image, wd, name, network, env_flags):
    cmd = ["docker", "run", "--rm", "--name", name, "-v", f"{wd}:/workspace"]
    if network:
        cmd += ["--network", network]
    cmd += env_flags + [image]
    return cmd


def parse_claude_stream(stdout: str) -> dict:
    """Parse `claude -p --output-format stream-json --verbose` output into metrics."""
    m = {"turns": None, "tool_calls": 0, "tool_errors": 0,
         "tokens_in": None, "tokens_out": None, "cost_usd": None, "result_subtype": None}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    m["tool_calls"] += 1
        elif t == "user":
            content = (ev.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                        m["tool_errors"] += 1
        elif t == "result":
            m["turns"] = ev.get("num_turns")
            m["cost_usd"] = ev.get("total_cost_usd")
            m["result_subtype"] = ev.get("subtype")
            usage = ev.get("usage") or {}
            m["tokens_in"] = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) \
                + (usage.get("cache_creation_input_tokens") or 0)
            m["tokens_out"] = usage.get("output_tokens")
    return m


def parse_vibe_json(stdout: str) -> dict:
    """Parse `vibe --output json` (a JSON array of messages at the end of stdout)."""
    m = {"turns": None, "tool_calls": 0, "tool_errors": 0,
         "tokens_in": None, "tokens_out": None, "cost_usd": None}
    # find the last JSON array in stdout
    start = stdout.find("[")
    msgs = None
    while start != -1:
        try:
            msgs = json.loads(stdout[start:])
            break
        except json.JSONDecodeError:
            start = stdout.find("[", start + 1)
    if not isinstance(msgs, list):
        return m
    turns = 0
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            turns += 1
            calls = msg.get("tool_calls") or []
            m["tool_calls"] += len(calls)
            usage = msg.get("usage") or {}
            for k_src, k_dst in [("prompt_tokens", "tokens_in"), ("completion_tokens", "tokens_out")]:
                if usage.get(k_src) is not None:
                    m[k_dst] = (m[k_dst] or 0) + usage[k_src]
        elif role == "tool":
            content = str(msg.get("content") or "")
            if re.search(r"(?i)^error|traceback|is_error", content[:200]):
                m["tool_errors"] += 1
    m["turns"] = turns or None
    return m


def parse_opencode_json(stdout: str) -> dict:
    """Parse `opencode run --format json` newline-delimited events."""
    m = {"turns": 0, "tool_calls": 0, "tool_errors": 0,
         "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    seen = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = ev.get("part") or {}
        t = ev.get("type")
        if t == "tool_use":
            seen = True
            m["tool_calls"] += 1
            if (part.get("state") or {}).get("status") == "error":
                m["tool_errors"] += 1
        elif t == "step_finish":
            seen = True
            m["turns"] += 1
            tok = part.get("tokens") or {}
            m["tokens_in"] += (tok.get("input") or 0) + ((tok.get("cache") or {}).get("read") or 0)
            m["tokens_out"] += tok.get("output") or 0
            m["cost_usd"] += part.get("cost") or 0
    if not seen:
        return {k: None for k in m}
    m["cost_usd"] = round(m["cost_usd"], 6) or None
    return m


def write_opencode_config(cfg_dir: Path) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {"edit": "allow", "bash": "allow"},
        "share": "disabled",
    }))
    cfg_dir.chmod(0o777)
    (cfg_dir / "opencode.json").chmod(0o666)


def run_arm_task(arm: int, model: str, task: str, run_id: str) -> dict:
    wd = prepare_workdir(task, run_id)
    prompt = (PROMPTS / f"{task}.md").read_text()
    name = f"bench-{run_id}-{task}"
    mistral_key = os.environ.get("MISTRAL_API_KEY", "")
    rec_notes = []

    if arm in (1, 2):
        flags = ["-p", prompt, "--output-format", "stream-json", "--verbose",
                 "--dangerously-skip-permissions", "--max-turns", MAX_TURNS]
        if arm == 2:
            master_key = os.environ.get("LITELLM_MASTER_KEY", "")
            env_flags = []
            for k, v in [("ANTHROPIC_BASE_URL", "http://litellm:4000"),
                         ("ANTHROPIC_AUTH_TOKEN", master_key),
                         ("ANTHROPIC_MODEL", model),
                         ("ANTHROPIC_SMALL_FAST_MODEL", model),
                         ("ANTHROPIC_DEFAULT_HAIKU_MODEL", model),
                         ("CLAUDE_CODE_SUBAGENT_MODEL", model)]:
                env_flags += ["-e", f"{k}={v}"]
            cmd = docker_base(IMAGES[arm], wd, name, "vexa-bench", env_flags) + flags
            code, out, err, wall = run_cmd(cmd, TASK_TIMEOUT_S)
        else:  # arm 1
            if os.environ.get("ANTHROPIC_API_KEY"):
                env_flags = ["-e", "ANTHROPIC_API_KEY"]  # value passed through, not embedded
                cmd = docker_base(IMAGES[arm], wd, name, None, env_flags) + ["--model", model] + flags
                code, out, err, wall = run_cmd(cmd, TASK_TIMEOUT_S)
                rec_notes.append("arm1 ran containerized with host ANTHROPIC_API_KEY")
            elif shutil.which("claude"):
                cmd = ["claude", "--model", model] + flags
                t0 = os.getcwd()
                os.chdir(wd)
                try:
                    code, out, err, wall = run_cmd(cmd, TASK_TIMEOUT_S)
                finally:
                    os.chdir(t0)
                rec_notes.append("arm1 ran on the host CLI with host Claude credentials")
            else:
                return blocked_record(arm, model, task, "no host Claude credentials (no ANTHROPIC_API_KEY, no claude CLI)")
        metrics = parse_claude_stream(out)
        if arm == 2:
            # The CLI prices runs as if the model were Claude; meaningless for a
            # Mistral upstream. The scorecard recomputes cost from tokens instead.
            metrics["cost_usd"] = None
        completed = code == 0 and metrics.get("result_subtype") == "success"
    elif arm == 3:
        vibe_dir = wd / ".vibe"
        vibe_dir.mkdir(exist_ok=True)
        # Model selection only — everything else stays Vibe defaults (no per-arm tuning).
        (vibe_dir / "config.toml").write_text(
            f'active_model = "{model}"\n\n[[models]]\nname = "{model}"\nprovider = "mistral"\n')
        vibe_dir.chmod(0o777)
        (vibe_dir / "config.toml").chmod(0o666)
        env_flags = ["-e", "MISTRAL_API_KEY"]
        cmd = docker_base(IMAGES[arm], wd, name, None, env_flags) + \
            ["--trust", "--agent", "auto-approve", "--prompt", prompt,
             "--output", "json", "--max-turns", MAX_TURNS]
        code, out, err, wall = run_cmd(cmd, TASK_TIMEOUT_S, {"MISTRAL_API_KEY": mistral_key})
        metrics = parse_vibe_json(out)
        completed = code == 0
    elif arm == 4:
        cfg = WORK / run_id / "opencode-config"
        write_opencode_config(cfg)
        env_flags = ["-e", "MISTRAL_API_KEY",
                     "-v", f"{cfg}:/home/agent/.config/opencode"]
        cmd = docker_base(IMAGES[arm], wd, name, None, env_flags) + \
            ["run", "--format", "json", "--model", f"mistral/{model}", prompt]
        code, out, err, wall = run_cmd(cmd, TASK_TIMEOUT_S, {"MISTRAL_API_KEY": mistral_key})
        metrics = parse_opencode_json(out)
        completed = code == 0
    else:
        raise SystemExit(f"unknown arm {arm}")

    # make sure no stray container survives a timeout
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    scores = scoring.score_task(task, wd, FIXTURES)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": run_id,
        "arm": arm,
        "cli": ARM_CLI[arm],
        "model": model,
        "task": task,
        "completed": bool(completed),
        "exit_code": code,
        "wall_clock_s": round(wall, 1),
        "scores": scores,
        "metrics": metrics,
        "notes": "; ".join(rec_notes),
    }
    # keep raw output for debugging (never contains the key — it is env-only)
    (wd / "_stdout.log").write_text(out[-200000:])
    (wd / "_stderr.log").write_text(err[-50000:])
    return record


def blocked_record(arm: int, model: str, task: str, reason: str) -> dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": "blocked",
        "arm": arm, "cli": ARM_CLI[arm], "model": model, "task": task,
        "completed": False, "exit_code": None, "wall_clock_s": None,
        "scores": None, "metrics": None, "notes": f"BLOCKED: {reason}",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", type=int, required=True, choices=[1, 2, 3, 4])
    ap.add_argument("--model", required=True)
    ap.add_argument("--tasks", default=",".join(TASKS))
    args = ap.parse_args()

    if args.arm in (2, 3, 4) and not os.environ.get("MISTRAL_API_KEY"):
        print("ERROR: MISTRAL_API_KEY not in env. Inject it via the sanctioned seam:\n"
              "  MISTRAL_API_KEY=$(cd <vexa-secrets> && bin/secret no-prod/mistral.enc.env MISTRAL_API_KEY)",
              file=sys.stderr)
        return 2
    if args.arm == 2 and not os.environ.get("LITELLM_MASTER_KEY"):
        print("ERROR: LITELLM_MASTER_KEY not in env (run via run.sh).", file=sys.stderr)
        return 2

    run_id = time.strftime("%y%m%d-%H%M%S") + f"-arm{args.arm}-" + uuid.uuid4().hex[:6]
    RUNS.mkdir(exist_ok=True)
    out_file = RUNS / f"arm{args.arm}-{re.sub(r'[^a-zA-Z0-9._-]', '_', args.model)}.jsonl"

    failures = 0
    for task in [t.strip() for t in args.tasks.split(",") if t.strip()]:
        log(f"arm {args.arm} ({ARM_CLI[args.arm]}) × {args.model} × {task} …")
        rec = run_arm_task(args.arm, args.model, task, run_id)
        with out_file.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        ok = rec["completed"]
        log(f"  → completed={ok} wall={rec['wall_clock_s']}s scores={rec['scores']}")
        if not ok:
            failures += 1
    log(f"results appended to {out_file.relative_to(HERE)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

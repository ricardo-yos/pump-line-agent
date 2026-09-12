"""
run_scenarios.py

Runs every task in scenarios/*.txt through the agent and saves the full
transcript (tool calls + final response) to runs/<timestamp>/<scenario>.md
- a manual regression log, not automated tests. You're not asserting
pass/fail here; you're building a set of comparable snapshots to eyeball
after changing the prompt, a tool, or the catalog, so you can tell "did
this scenario's answer change, and is the change better or worse?"
without re-running every case from scratch and re-reading raw JSON.

Each scenario file's content is used verbatim as the task. Add a new
scenario by dropping a new .txt file in scenarios/ - no code change
needed. Name files with a numeric prefix (01_, 02_, ...) so they sort
and print in a stable, readable order.

Usage:
    python run_scenarios.py                  # all scenarios
    python run_scenarios.py 03 04             # only scenarios whose filename starts with 03 or 04
"""
import io
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agent import make_client, run_agent, SYSTEM, TOOLS, USAGE
import tools as tools_module


class _Tee(io.TextIOBase):
    """Writes to both the real stdout (so you see progress live) and an
    in-memory buffer (so the same output can be saved to the scenario's
    result file)."""
    def __init__(self, real_stdout, buffer):
        self.real_stdout = real_stdout
        self.buffer = buffer

    def write(self, s):
        self.real_stdout.write(s)
        self.buffer.write(s)
        return len(s)

    def flush(self):
        self.real_stdout.flush()


def run_one_scenario(client, model: str, scenario_path: Path) -> str:
    """Runs a single scenario and returns the full transcript as markdown."""
    task = scenario_path.read_text(encoding="utf-8").strip()

    # Fresh state per scenario - a stale grid/log from a previous scenario
    # shouldn't leak into this one (e.g. a pump-only scenario accidentally
    # reading a pipe grid computed for a different scenario).
    tools_module.TOOL_CALL_LOG.clear()
    tools_module.LAST_PIPE_PUMP_GRID.clear()
    USAGE["iterations"] = 0
    USAGE["input_tokens"] = 0
    USAGE["output_tokens"] = 0
    USAGE["per_iter"] = []

    buffer = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, buffer)
    try:
        final = run_agent(client, model, SYSTEM, TOOLS, task)
    finally:
        sys.stdout = real_stdout

    tool_call_log = "\n".join(
        f"- `{e['tool']}({e['args']})` -> {e['result_chars']} chars"
        for e in tools_module.TOOL_CALL_LOG
    ) or "(no tool calls)"

    return (
        f"# {scenario_path.stem}\n\n"
        f"## Task\n\n{task}\n\n"
        f"## Tool calls\n\n{tool_call_log}\n\n"
        f"## Console output\n\n```\n{buffer.getvalue()}\n```\n\n"
        f"## Final response\n\n{final}\n\n"
        f"## Usage\n\n"
        f"- iterations: {USAGE['iterations']}\n"
        f"- input_tokens: {USAGE['input_tokens']}\n"
        f"- output_tokens: {USAGE['output_tokens']}\n"
    )


def main():
    load_dotenv()
    base_url = os.environ.get("LLM_BASE_URL") or ""
    model = os.environ.get("LLM_AGENT_MODEL", "gpt-5-mini")
    client = make_client(base_url)

    scenarios_dir = Path("scenarios")
    all_scenarios = sorted(scenarios_dir.glob("*.txt"))

    filters = sys.argv[1:]
    if filters:
        all_scenarios = [s for s in all_scenarios if any(s.stem.startswith(f) for f in filters)]

    if not all_scenarios:
        print("No matching scenarios found.")
        return

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    for scenario_path in all_scenarios:
        print(f"\n{'=' * 70}\nRunning {scenario_path.stem}\n{'=' * 70}")
        transcript = run_one_scenario(client, model, scenario_path)
        out_path = run_dir / f"{scenario_path.stem}.md"
        out_path.write_text(transcript, encoding="utf-8")
        print(f"\nSaved to {out_path}")

    print(f"\nAll results in {run_dir}/")


if __name__ == "__main__":
    main()

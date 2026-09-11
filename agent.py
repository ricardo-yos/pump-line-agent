"""
pump-line-agent
 
An agent that selects a centrifugal pump - and, in its pipe+pump mode,
sizes the suction/discharge piping around it - for a water-transfer
duty between two reservoirs. See README.md at the project root for the
two modes, the engineering decisions behind the catalog/physics, and
known limitations (notably: no context compaction, see below).
 
Same shape as episodes/02-tools/agent.py in the reference architecture
(readytensor/building-agents): a while loop, tools dispatched by name
from tools.py, a @tool decorator that builds each tool's JSON schema
from its signature. Completion is the natural stop: the loop ends when
the model emits no tool calls.
 
The task is a single free-text description (see main()'s `input("Task: ")`
prompt), not structured fields - the agent figures out from the
description which mode applies and what's missing (see system_prompt.md).
 
Note on what's intentionally NOT here, unlike the reference agent.py:
that episode's agent edits a real codebase, so its main() resets a
SANDBOX from an INITIAL/ copy before every run. This agent only reads
a static pump/pipe catalog and runs numeric evaluations - there's no
project state to reset, so that machinery is omitted rather than kept
as unused scaffolding. Also not here: Episode 3's context compaction -
messages accumulate for the life of a run uncompacted, which can hit a
provider's per-minute token cap on a long enough multi-tool-call run
(see README.md's "Known limitations").
"""
import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI
from tiktoken import get_encoding

import tools as tools_module  # aliased: run_agent's `tools` parameter takes the canonical name; the loop sets tools_module.CURRENT_ROUND each turn
from tools import TOOLS, write_tool_telemetry
from pump_catalog import PUMP_CATALOG

from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def make_client(base_url: str) -> OpenAI:
    """Connect to the LLM provider behind `base_url` — any OpenAI-compatible
    endpoint. The matching API key is picked from the environment by provider,
    so switching providers means changing only LLM_BASE_URL, never moving keys
    around. Anything OpenAI-compatible (Together, DeepSeek, OpenRouter, …)
    falls through to OPENAI_API_KEY."""
    by_provider = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "googleapis": "GOOGLE_API_KEY",
        "manus": "MANUS_API_KEY",
    }
    key_var = "OPENAI_API_KEY"
    for fragment, provider_key_var in by_provider.items():
        if fragment in base_url:
            key_var = provider_key_var
            break
    return OpenAI(api_key=os.environ.get(key_var), base_url=base_url or None)


# The system prompt lives in system_prompt.md next to this file: prompt text
# is configuration, not loop logic.
SYSTEM = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

# --- Usage telemetry: token counts per run, recorded by run_agent as it goes.
USAGE = {
    "iterations": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "per_iter": [],  # {model_in, model_out, tools, tools_out} per round
}


def write_metrics(model: str, system: str, task: str):
    """Write this run's token usage to metrics.json. Recording only."""
    metrics = {
        "agents": [{"label": "agent", **USAGE}],
        "inputs": {"system": system, "task": task},
        "config": {"MODEL": model},
    }
    with open("metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)


# tiktoken encoder for the per-round tool-result token count (tools_out).
_TOKENIZER = get_encoding("cl100k_base")


def _count_tokens(messages):
    return len(_TOKENIZER.encode("\n".join(str(m.get("content") or "") for m in messages)))


def run_agent(client, model: str, system: str, tools: list, task: str) -> str:
    """Run the agent loop on `task` until the model stops requesting tool
    calls; return its final message. Records token usage into USAGE along
    the way."""
    tools_by_name = {t.__name__: t for t in tools}
    tool_defs = [t.tool_definition for t in tools]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    iteration = 0
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", 2000))
    # Optional: some reasoning models (e.g. Groq's qwen/qwen3.6-27b) support a
    # reasoning_effort param that can disable hidden "thinking" tokens
    # entirely ("none") - those tokens count against providers' output-token
    # rate limits without ever being visible to the application.
    # Left unset by default since not every provider/model accepts this
    # param; set LLM_REASONING_EFFORT to opt in.
    reasoning_effort = os.environ.get("LLM_REASONING_EFFORT")

    while True:
        iteration += 1
        tools_module.CURRENT_ROUND = iteration  # tag tool calls with the round they happen in
        extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tool_defs, max_tokens=max_tokens, **extra,
        )
        usage = resp.usage
        USAGE["iterations"] = iteration
        USAGE["input_tokens"] += usage.prompt_tokens
        USAGE["output_tokens"] += usage.completion_tokens
        USAGE["per_iter"].append({"model_in": usage.prompt_tokens, "model_out": usage.completion_tokens, "tools": 0, "tools_out": 0})

        msg = resp.choices[0].message
        USAGE["per_iter"][-1]["tools"] = len(msg.tool_calls or [])
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return msg.content or ""

        round_tool_msgs = []
        for tc in msg.tool_calls:
            try:
                fn = tools_by_name[tc.function.name]
                args = json.loads(tc.function.arguments)
                parts = []
                for k, v in args.items():
                    if len(repr(v)) < 60:
                        parts.append(f"{k}={v!r}")
                    else:
                        parts.append(f"{k}=<{len(str(v))} chars>")
                print(f"> {tc.function.name}({', '.join(parts)})")
                result = fn(**args)
            except (TypeError, KeyError, json.JSONDecodeError, ValueError) as e:
                # Tool errors come back to the model as the tool result, not
                # as an agent crash. The model can self-correct next iteration.
                result = f"Error executing {tc.function.name}: {type(e).__name__}: {e}"
                print(f"  ! {result}")
            preview = result if len(result) < 5000 else result[:5000] + "...[truncated]"
            print(f"  {preview}\n")
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result}
            round_tool_msgs.append(tool_msg)
            messages.append(tool_msg)

        USAGE["per_iter"][-1]["tools_out"] = _count_tokens(round_tool_msgs)


def main():
    load_dotenv()
    base_url = os.environ.get("LLM_BASE_URL") or ""
    model = os.environ.get("LLM_AGENT_MODEL", "gpt-5-mini")
    client = make_client(base_url)

    print(f"Catalog loaded: {len(PUMP_CATALOG)} pump curves.")
    task = input("Task: ").strip()

    print(f"USER: {task}\n")
    final = run_agent(client, model, SYSTEM, TOOLS, task)
    print(f"\n=== FINAL RESPONSE ===\n\n{final}")
    write_tool_telemetry()
    write_metrics(model, SYSTEM, task)


if __name__ == "__main__":
    main()

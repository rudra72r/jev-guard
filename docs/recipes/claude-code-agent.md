# Guarding a Claude Code agent

Coding agents are where one bad action costs the most: `rm -rf`, a force-push, an API key
pasted into a commit, or a web page that tells the agent to do any of those. jev-guard plugs
into Claude Code's tool hooks with one command.

## Set it up

```bash
pip install "jev-guard[cli]"
export TYPESAFE_API_KEY=sk-...   # see "Backend" below before choosing something else
```

Add the hooks to `.claude/settings.json` in your project (or `~/.claude/settings.json` for
all projects):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit|mcp__.*",
        "hooks": [{ "type": "command", "command": "jev-guard hook claude-code", "timeout": 30 }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "WebFetch|WebSearch|Read|mcp__.*",
        "hooks": [{ "type": "command", "command": "jev-guard hook claude-code", "timeout": 30 }]
      }
    ]
  }
}
```

## What it does

**Before a tool runs** (PreToolUse), the tool name and input are checked with the
`agent_tools` policy:

- **block** (destructive commands, touching credentials, or two risky signals together):
  the call doesn't run, and Claude sees the reason.
- **review** (sending data out, consequential actions): Claude Code asks you to confirm.
- **allow**: the hook prints nothing, so your normal Claude Code permission rules still
  apply. It never auto-approves anything.

**After a tool returns** (PostToolUse), the output is checked before Claude acts on it.
Content that tries to redirect the agent, or asks it to send data somewhere, stops the turn
with a warning to treat that content as untrusted. This is the defence against indirect
prompt injection from web pages, READMEs, issues, and MCP servers.

## Choices

- **Fail closed:** `jev-guard hook claude-code --fail-closed` blocks when Jev can't be
  reached. By default the hook fails open (the tool runs, and a note goes to Claude Code's
  debug log), so a Jev outage doesn't stop your work.
- **Backend:** a hook runs as a *fresh process on every tool call*, so per-process startup is
  the whole latency budget. Jev (70–500 ms) is the right fit. The offline backend is not:
  it reloads its models every time, about 23 s, which alone would exceed the `timeout: 30`
  above. If you want to stay off a hosted API, point the hook at a local server instead —
  `JEV_GUARD_BACKEND=ollama:llama3.1` keeps the model resident between calls.
- **Your own policy:** `--policy ./my_agent.yaml`, starting from `extends: agent_tools`.
- **Cost:** each checked tool use is one Jev call (about $0.00002), and large outputs are
  checked in 40,000-character windows. Narrow the matchers if you want fewer checks.

!!! note "Why blocks use exit code 2"
    Claude Code treats hook JSON that fails its schema as a non-blocking error and lets the
    action proceed. So jev-guard blocks with exit code 2, which always blocks, and uses
    JSON only for "ask". A formatting mistake can never turn into a silent allow.

## Without Claude Code

The same checks work in any agent loop. See [Agents and multi-turn attacks](../agents.md):

```python
from jev_guard.agents import ToolGuard

tools = ToolGuard()


def run_shell(command: str, task: str) -> str:
    verdict = tools.check_tool_call("run_shell", {"command": command}, user_request=task)
    if verdict.blocked:
        return f"Blocked by policy: {'; '.join(verdict.reasons)}. Do not retry this command."
    if verdict.action == "review":
        return f"Needs human approval ({'; '.join(verdict.reasons)}). Ask the user first."
    return subprocess_run(command)  # your real executor
```

Returning the reason to the model, instead of raising, lets the agent explain itself or pick
a safer command. [`examples/02_anthropic_agent.py`](https://github.com/rudra72r/jev-guard/blob/main/examples/02_anthropic_agent.py)
has a full Claude tool-use loop that only prints approved commands, never runs them.

For checking generated *code* (hardcoded secrets, SQL injection, unsafe deserialization),
use the `coding_agent` policy's output checks:
`Guard(policy="coding_agent").check_output(task, diff)`.

# Guarding a Claude Code agent

Coding agents are the case where one bad action costs the most: `rm -rf`, a force-push, or an API key pasted into a commit. The `coding_agent` policy checks two things:

- **each command before it runs** (input stage): destructive commands, secret access, network writes, privilege escalation
- **generated code before it's applied** (output stage): suggested destructive commands, hardcoded secrets, unsafe deserialization, SQL injection

## Check every tool call

Put the check in your tool executor, so the model can't route around it:

```python
from jev_guard import Guard

guard = Guard(policy="coding_agent")


def run_shell(command: str) -> str:
    verdict = guard.check_input(command)
    if verdict.blocked:
        return f"Blocked by policy: {'; '.join(verdict.reasons)}. Do not retry this command."
    if verdict.action == "review":
        return f"Needs human approval ({'; '.join(verdict.reasons)}). Ask the user first."
    return subprocess_run(command)  # your real executor
```

Returning the reason to the model, instead of raising, lets the agent explain itself or choose a safer command. A full Claude tool-use loop is in [`examples/02_anthropic_agent.py`](https://github.com/rudra72r/jev-guard/blob/main/examples/02_anthropic_agent.py). It only prints approved commands, never runs them.

## Check generated code before applying it

```python
verdict = guard.check_output(task_description, proposed_diff)
if verdict.blocked:
    reject_patch(verdict.reasons)  # e.g. hardcoded_secret_present: 0.93 > 0.75 (critical)
```

## Claude Code hooks

Claude Code can run a command before each tool call (a `PreToolUse` hook) and block the call when the command exits with code 2. A small script bridges jev-guard to that:

```python
#!/usr/bin/env python3
"""PreToolUse hook: block risky Bash commands with jev-guard."""

import json
import sys

from jev_guard import Guard, JevAPIError

event = json.load(sys.stdin)
if event.get("tool_name") != "Bash":
    sys.exit(0)
command = event.get("tool_input", {}).get("command", "")
try:
    verdict = Guard(policy="coding_agent").check_input(command)
except JevAPIError as err:
    print(f"jev-guard unavailable: {err}", file=sys.stderr)
    sys.exit(0)  # fail open here; change to 2 to fail closed
if verdict.blocked:
    print("Blocked by jev-guard: " + "; ".join(verdict.reasons), file=sys.stderr)
    sys.exit(2)
```

Register it as a `PreToolUse` hook for the `Bash` tool in your Claude Code settings. Check Claude Code's hooks documentation for the current event fields and settings format.

## Tuning

- **Sandboxed agents** (throwaway containers): raise the destructive-command thresholds to 0.9 to cut interruptions.
- **Agents with production credentials**: start from [`policies/coding_agent_prod.yaml`](https://github.com/rudra72r/jev-guard/blob/main/policies/coding_agent_prod.yaml), where any single high-severity signal blocks and `sudo` is critical.

`sudo` is only `high` in the default policy, so it goes to review rather than blocking, because installing packages legitimately needs it.

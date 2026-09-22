"""Read LLM logs in the shapes `jev-guard scan` accepts, detected from the first record.

- **native**: ``{"input": ..., "output": ..., "context": [...], "label": ...}``
- **langfuse**: trace or observation exports (``traceId`` / ``projectId`` / ``sessionId`` keys)
- **arize**: OpenInference span exports, flat (``"attributes.input.value"``) or nested
  (``{"attributes": {"input.value": ...}}``)

Langfuse and Arize payloads vary between SDK versions, so extraction is best-effort: chat
message lists resolve to the last user / assistant message, JSON strings are decoded, and
anything unrecognised is kept as its JSON text rather than dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

LogFormat = Literal["native", "langfuse", "arize"]
Label = str | dict[str, bool]

_LANGFUSE_KEYS = {"traceId", "projectId", "sessionId"}
_TEXT_KEYS = ("content", "text", "value", "answer", "output", "input", "query", "question")
_USER_ROLES = {"user", "human"}
_ASSISTANT_ROLES = {"assistant", "ai", "model"}


@dataclass(frozen=True, slots=True)
class LogRecord:
    line: int
    input: str
    output: str | None = None
    context: list[str] | None = None
    label: Label | None = None


@dataclass(frozen=True, slots=True)
class LogFile:
    format: LogFormat
    records: list[LogRecord]
    warnings: list[str]


def read_log_file(path: str | Path) -> LogFile:
    records: list[LogRecord] = []
    warnings: list[str] = []
    fmt: LogFormat | None = None
    for line_no, raw in _json_lines(Path(path), warnings):
        if fmt is None:
            fmt = detect_format(raw)
        record = _parse(fmt, line_no, raw)
        if record is None:
            warnings.append(f"line {line_no}: no input or output text found, skipped")
        else:
            records.append(record)
    return LogFile(fmt or "native", records, warnings)


def detect_format(first: Mapping[str, Any]) -> LogFormat:
    keys = set(first)
    if any(k.startswith("attributes.") for k in keys) or "context.span_id" in keys:
        return "arize"
    attributes = first.get("attributes")
    if isinstance(attributes, Mapping) and any(
        k.startswith(("input.", "output.", "openinference.")) for k in attributes
    ):
        return "arize"
    if keys & _LANGFUSE_KEYS:
        return "langfuse"
    return "native"


# --- parsing ------------------------------------------------------------------------------


def _json_lines(path: Path, warnings: list[str]) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as err:
                warnings.append(f"line {line_no}: not valid JSON ({err.msg}), skipped")
                continue
            if not isinstance(value, dict):
                warnings.append(f"line {line_no}: expected a JSON object, skipped")
                continue
            yield line_no, value


def _parse(fmt: LogFormat, line: int, raw: dict[str, Any]) -> LogRecord | None:
    if fmt == "arize":
        user_text = _text(_attr(raw, "input.value"), _USER_ROLES) or _text(
            _attr(raw, "llm.input_messages"), _USER_ROLES
        )
        reply = _text(_attr(raw, "output.value"), _ASSISTANT_ROLES) or _text(
            _attr(raw, "llm.output_messages"), _ASSISTANT_ROLES
        )
        context = _documents(_attr(raw, "retrieval.documents"))
    else:
        user_text = _text(raw.get("input"), _USER_ROLES)
        reply = _text(raw.get("output"), _ASSISTANT_ROLES)
        raw_metadata = raw.get("metadata")
        metadata: Mapping[str, Any] = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        context = _documents(raw.get("context", metadata.get("context")))
    if not user_text and not reply:
        return None
    return LogRecord(
        line=line,
        input=user_text,
        output=reply or None,
        context=context,
        label=_label(raw.get("label")),
    )


def _attr(raw: Mapping[str, Any], name: str) -> Any:
    """An OpenInference attribute from a flat or nested export."""
    flat = raw.get(f"attributes.{name}")
    if flat is not None:
        return flat
    attributes = raw.get("attributes")
    if isinstance(attributes, Mapping):
        if name in attributes:
            return attributes[name]
        node: Any = attributes
        for part in name.split("."):
            node = node.get(part) if isinstance(node, Mapping) else None
        return node
    return None


def _text(value: Any, roles: set[str]) -> str:  # noqa: PLR0911 (one return per input shape)
    """Readable text from a string, JSON string, message, message list, or payload object."""
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "[{":
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                return value
            return _text(decoded, roles) or value
        return value
    if isinstance(value, list):
        return _from_messages(value, roles)
    if isinstance(value, Mapping):
        if "messages" in value:
            return _text(value["messages"], roles)
        for key in _TEXT_KEYS:
            if key in value and value[key] is not None:
                return _text(value[key], roles)
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _role(message: Mapping[str, Any]) -> str | None:
    role = message.get("role", message.get("message.role", message.get("type")))
    return str(role).lower() if role is not None else None


def _from_messages(messages: list[Any], roles: set[str]) -> str:
    for message in reversed(messages):
        inner = message.get("message", message) if isinstance(message, Mapping) else message
        if isinstance(inner, Mapping) and _role(inner) in roles:
            content = inner.get("content", inner.get("message.content"))
            return _parts(content)
    texts = [_parts(m) for m in messages if isinstance(m, str | Mapping)]
    return "\n".join(t for t in texts if t)


def _parts(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) if isinstance(p, Mapping) else str(p) for p in content
        ).strip()
    if isinstance(content, Mapping):
        return str(content.get("text", content.get("content", "")))
    return ""


def _documents(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        docs = []
        for item in value:
            if isinstance(item, Mapping):
                docs.append(
                    str(
                        item.get(
                            "document.content", item.get("page_content", item.get("content", ""))
                        )
                    )
                )
            else:
                docs.append(str(item))
        return [d for d in docs if d] or None
    return None


def _label(value: Any) -> Label | None:
    if isinstance(value, bool):
        return "block" if value else "allow"
    if isinstance(value, str) and value:
        return value.lower()
    if isinstance(value, Mapping):
        return {str(k): bool(v) for k, v in value.items()}
    return None

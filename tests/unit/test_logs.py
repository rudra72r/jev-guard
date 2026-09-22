"""Log parsing and format detection for `jev-guard scan`."""

from __future__ import annotations

import json

import pytest

from jev_guard.eval.logs import detect_format, read_log_file


def write_jsonl(path, rows):
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


def test_native_format(tmp_path):
    path = write_jsonl(
        tmp_path / "logs.jsonl",
        [
            {"input": "hi", "output": "hello", "context": ["doc"], "label": "allow"},
            {"input": "q2", "label": {"is_prompt_injection": True}},
            {"input": "q3", "output": "a3", "label": True},
        ],
    )
    log = read_log_file(path)
    assert log.format == "native"
    assert log.warnings == []
    first, second, third = log.records
    assert (first.line, first.input, first.output, first.context) == (1, "hi", "hello", ["doc"])
    assert first.label == "allow"
    assert second.output is None
    assert second.label == {"is_prompt_injection": True}
    assert third.label == "block"


def test_bad_lines_are_warned_and_skipped(tmp_path):
    path = write_jsonl(tmp_path / "l.jsonl", [{"input": "ok"}, "{not json", "[1, 2]", {"x": 1}, ""])
    log = read_log_file(path)
    assert [r.input for r in log.records] == ["ok"]
    assert log.warnings[0].startswith("line 2: not valid JSON")
    assert log.warnings[1] == "line 3: expected a JSON object, skipped"
    assert log.warnings[2] == "line 4: no input or output text found, skipped"


def test_langfuse_trace_export(tmp_path):
    path = write_jsonl(
        tmp_path / "traces.jsonl",
        [
            {
                "id": "t1",
                "projectId": "p",
                "timestamp": "2026-09-20T10:00:00Z",
                "input": {
                    "messages": [
                        {"role": "system", "content": "be nice"},
                        {"role": "user", "content": "Where's my order?"},
                    ]
                },
                "output": {"role": "assistant", "content": "It ships Monday."},
                "metadata": {"context": ["Orders ship in 2 days."]},
            },
            {"id": "t2", "projectId": "p", "input": "plain question", "output": "plain answer"},
        ],
    )
    log = read_log_file(path)
    assert log.format == "langfuse"
    first, second = log.records
    assert first.input == "Where's my order?"
    assert first.output == "It ships Monday."
    assert first.context == ["Orders ship in 2 days."]
    assert (second.input, second.output) == ("plain question", "plain answer")


def test_langfuse_observation_with_json_string_and_parts(tmp_path):
    messages = [{"role": "user", "content": [{"type": "text", "text": "part one"}]}]
    path = write_jsonl(
        tmp_path / "obs.jsonl",
        [
            {
                "traceId": "t",
                "type": "GENERATION",
                "input": json.dumps(messages),
                "output": {"text": "done"},
            }
        ],
    )
    (record,) = read_log_file(path).records
    assert record.input == "part one"
    assert record.output == "done"


def test_arize_flat_export(tmp_path):
    path = write_jsonl(
        tmp_path / "spans.jsonl",
        [
            {
                "context.span_id": "s1",
                "attributes.input.value": "What is the fee?",
                "attributes.output.value": "2%.",
                "attributes.retrieval.documents": [{"document.content": "Fee: 2%"}],
            }
        ],
    )
    log = read_log_file(path)
    assert log.format == "arize"
    (record,) = log.records
    assert (record.input, record.output, record.context) == ("What is the fee?", "2%.", ["Fee: 2%"])


def test_arize_nested_messages(tmp_path):
    path = write_jsonl(
        tmp_path / "spans.jsonl",
        [
            {
                "name": "llm",
                "attributes": {
                    "openinference.span.kind": "LLM",
                    "llm": {
                        "input_messages": [{"message.role": "user", "message.content": "hey"}],
                        "output_messages": [
                            {"message.role": "assistant", "message.content": "hello!"}
                        ],
                    },
                },
            }
        ],
    )
    log = read_log_file(path)
    assert log.format == "arize"
    assert (log.records[0].input, log.records[0].output) == ("hey", "hello!")


@pytest.mark.parametrize(
    ("first", "fmt"),
    [
        ({"input": "x"}, "native"),
        ({"sessionId": "s", "input": "x"}, "langfuse"),
        ({"attributes.input.value": "x"}, "arize"),
        ({"attributes": {"input.value": "x"}}, "arize"),
        ({"attributes": {"other": 1}, "input": "x"}, "native"),
    ],
)
def test_detect_format(first, fmt):
    assert detect_format(first) == fmt


def test_unknown_payload_is_kept_as_json(tmp_path):
    path = write_jsonl(tmp_path / "l.jsonl", [{"input": {"foo": "bar"}, "output": 42}])
    (record,) = read_log_file(path).records
    assert record.input == '{"foo": "bar"}'
    assert record.output == "42"


def test_messages_without_matching_role_fall_back_to_all_text(tmp_path):
    path = write_jsonl(tmp_path / "l.jsonl", [{"input": ["line one", {"text": "line two"}]}])
    (record,) = read_log_file(path).records
    assert record.input == "line one\nline two"


def test_context_as_string(tmp_path):
    path = write_jsonl(tmp_path / "l.jsonl", [{"input": "q", "output": "a", "context": "doc"}])
    assert read_log_file(path).records[0].context == ["doc"]

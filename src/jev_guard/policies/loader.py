"""YAML <-> Policy, with a linter that reports every problem with its line number.

A policy file either defines everything itself or starts from a builtin with ``extends``::

    name: strict
    extends: general          # optional: start from a builtin
    block_threshold: 3.0
    input:
      is_prompt_injection:
        threshold: 0.7        # merged into the builtin question, other fields kept
      contains_pii: null      # removes the question

Only ``yaml.safe_load`` is used: policy files are data and can never execute code.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jev_guard.errors import PolicyError
from jev_guard.policies.base import Policy
from jev_guard.types import GuardStage, QuestionSpec

Path_ = tuple[str | int, ...]

_TOP_LEVEL = set(Policy.model_fields) | {"extends"}
_QUESTION_FIELDS = set(QuestionSpec.model_fields)
_ENUMS: dict[str, tuple[str, ...]] = {
    "type": ("noul", "choice", "score"),
    "severity": ("low", "medium", "high", "critical"),
    "comparator": (">", ">=", "<", "<="),
    "risk_when": ("high", "low"),
}
_STAGES: tuple[GuardStage, ...] = ("input", "output")


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem in a policy file. ``line`` is 1-based, or None when unknown."""

    line: int | None
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}" if self.line else self.message


class PolicyFileError(PolicyError):
    """A policy file failed validation. ``issues`` lists every problem found."""

    def __init__(self, source: str, issues: list[Issue]) -> None:
        self.source = source
        self.issues = issues
        details = "\n".join(f"  {source} {issue}" for issue in issues)
        super().__init__(
            f"{len(issues)} problem(s) in policy file {source}:\n{details}",
            hint=f"Fix the lines above, then run `jev-guard policy validate {source}`.",
        )


# --- public API ---------------------------------------------------------------------------


def load_policy_file(path: str | os.PathLike[str]) -> Policy:
    """Read and validate a policy file. Raises ``PolicyFileError`` listing every problem."""
    source = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as err:
        raise PolicyError(
            f"Can't read policy file {source}: {err.strerror or err}",
            hint="Check the path, or use a builtin name like 'general'.",
        ) from err
    return load_policy_text(text, source=source)


def load_policy_text(text: str, *, source: str = "<string>") -> Policy:
    policy, issues = validate_policy_text(text)
    if issues or policy is None:
        raise PolicyFileError(source, issues)
    return policy


def validate_policy_file(path: str | os.PathLike[str]) -> list[Issue]:
    """Every problem in a policy file; an empty list means it's valid."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as err:
        return [Issue(None, f"can't read file: {err.strerror or err}")]
    return validate_policy_text(text)[1]


def validate_policy_text(text: str) -> tuple[Policy | None, list[Issue]]:
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        problem = getattr(err, "problem", None) or str(err)
        return None, [Issue(mark.line + 1 if mark else None, f"invalid YAML: {problem}")]

    lines = _line_map(root)
    if not isinstance(data, dict):
        return None, [Issue(1, "a policy file must be a mapping with at least `name`")]

    issues = list(_lint(data, lines))
    if issues:
        return None, issues

    merged, policy_cls = _merge_with_base(data)
    try:
        return policy_cls.model_validate(merged), []
    except ValidationError as err:
        return None, [_issue_from_pydantic(e, lines) for e in err.errors(include_url=False)]


def dump_policy(policy: Policy) -> str:
    """The policy as YAML that ``load_policy_text`` reads back to an equivalent policy."""
    data: dict[str, Any] = {
        "name": policy.name,
        "version": policy.version,
        "description": policy.description,
        "review_threshold": policy.review_threshold,
        "block_threshold": policy.block_threshold,
    }
    if policy.context_fallback is not None:
        data["context_fallback"] = policy.context_fallback
    data["stream_strategy"] = policy.stream_strategy
    data["stream_check_every"] = policy.stream_check_every
    data["default_suggested_response"] = policy.default_suggested_response
    if policy.suggested_responses:
        data["suggested_responses"] = dict(policy.suggested_responses)
    for stage in _STAGES:
        data[stage] = {
            name: _dump_question(spec) for name, spec in policy.questions_for(stage).items()
        }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=96)


# --- internals ----------------------------------------------------------------------------

_ALWAYS_DUMPED = ("type", "instructions", "severity", "threshold")
_DUMP_ORDER = (
    "type",
    "instructions",
    "criteria",
    "flag",
    "severity",
    "weight",
    "threshold",
    "comparator",
    "risk_when",
)


def _dump_question(spec: QuestionSpec) -> dict[str, Any]:
    raw = spec.model_dump()
    out: dict[str, Any] = {}
    for key in _DUMP_ORDER:
        default = QuestionSpec.model_fields[key].get_default(call_default_factory=True)
        if key in _ALWAYS_DUMPED or raw[key] != default:
            out[key] = raw[key]
    return out


def _line_map(node: yaml.Node | None, path: Path_ = ()) -> dict[Path_, int]:
    """Map each key path (e.g. ("input", "is_prompt_injection", "type")) to its 1-based line."""
    lines: dict[Path_, int] = {}
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            child = (*path, str(key_node.value))
            lines[child] = key_node.start_mark.line + 1
            lines.update(_line_map(value_node, child))
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            child = (*path, index)
            lines[child] = item.start_mark.line + 1
            lines.update(_line_map(item, child))
    return lines


def _line_for(path: Iterable[str | int], lines: dict[Path_, int]) -> int | None:
    parts = tuple(path)
    while parts:
        if parts in lines:
            return lines[parts]
        parts = parts[:-1]
    return None


def _suggest(value: str, options: Iterable[str]) -> str:
    options = list(options)
    if value[::-1] in options:  # "=>" -> ">=", "=<" -> "<="
        return f" — did you mean {value[::-1]!r}?"
    match = difflib.get_close_matches(value, options, n=1, cutoff=0.5)
    return f" — did you mean {match[0]!r}?" if match else ""


def _lint(data: dict[str, Any], lines: dict[Path_, int]) -> Iterable[Issue]:
    for key in data:
        if key not in _TOP_LEVEL:
            yield Issue(
                _line_for((key,), lines), f"unknown field {key!r}{_suggest(str(key), _TOP_LEVEL)}"
            )

    extends = data.get("extends")
    if extends is not None:
        available = Policy.available()
        if extends not in available:
            yield Issue(
                _line_for(("extends",), lines),
                f"extends unknown builtin {extends!r}{_suggest(str(extends), available)}",
            )
    elif "name" not in data:
        yield Issue(1, "missing required field 'name'")

    for stage in _STAGES:
        yield from _lint_stage(stage, data.get(stage), lines, partial=extends is not None)


def _lint_stage(
    stage: str, questions: Any, lines: dict[Path_, int], *, partial: bool
) -> Iterable[Issue]:
    if questions is None:
        return
    if not isinstance(questions, dict):
        yield Issue(_line_for((stage,), lines), f"{stage} must map question names to questions")
        return
    for name, question in questions.items():
        where = (stage, str(name))
        if question is None and partial:
            continue  # removes an inherited question
        if not isinstance(question, dict):
            yield Issue(_line_for(where, lines), f"question {name!r} must be a mapping")
            continue
        for field in question:
            if field not in _QUESTION_FIELDS:
                yield Issue(
                    _line_for((*where, field), lines),
                    f"unknown question field {field!r}{_suggest(str(field), _QUESTION_FIELDS)}",
                )
        for field, allowed in _ENUMS.items():
            if field in question and question[field] not in allowed:
                label = "question type" if field == "type" else field
                yield Issue(
                    _line_for((*where, field), lines),
                    f"unknown {label} {question[field]!r}{_suggest(str(question[field]), allowed)}",
                )


def _merge_with_base(data: dict[str, Any]) -> tuple[dict[str, Any], type[Policy]]:
    extends = data.get("extends")
    own = {k: v for k, v in data.items() if k != "extends"}
    if extends is None:
        return own, Policy

    base = Policy.from_builtin(extends)
    merged = base.model_dump()
    for key, value in own.items():
        if key in _STAGES and isinstance(value, dict):
            stage = dict(merged[key])
            for name, question in value.items():
                if question is None:
                    stage.pop(name, None)
                elif name in stage:
                    stage[name] = {**stage[name], **question}
                else:
                    stage[name] = question
            merged[key] = stage
        else:
            merged[key] = value
    return merged, type(base)


def _issue_from_pydantic(error: Any, lines: dict[Path_, int]) -> Issue:
    loc = tuple(error["loc"])
    message = str(error["msg"]).removeprefix("Value error, ")
    where = ".".join(str(p) for p in loc)
    return Issue(_line_for(loc, lines), f"{where}: {message}" if where else message)

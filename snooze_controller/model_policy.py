from __future__ import annotations

"""Fail-closed model policy for Codex Snooze experiments.

Capability experiments are valid only when every model-facing path is
explicitly pinned to GPT-5.6 Luna and the runtime reports the same model for
the thread and turn.  A configured value is not treated as runtime
attestation; missing telemetry is a failure.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MODEL_POLICY = "LUNA_ONLY"
LUNA_MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "medium"
SUBAGENT_POLICY = "DISABLED"


class ModelPolicyError(RuntimeError):
    """Raised when a model-facing command or request violates LUNA_ONLY."""


def _string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _config_value(value: str) -> Tuple[Optional[str], Optional[str]]:
    if "=" not in value:
        return None, None
    key, raw = value.split("=", 1)
    key = key.strip()
    raw = raw.strip().strip('"').strip("'")
    return key, raw


def _validate_explicit_flags(command: Sequence[str]) -> None:
    values = list(command)
    for index, token in enumerate(values):
        if token in {"-m", "--model"}:
            if index + 1 >= len(values):
                raise ModelPolicyError(f"{token} has no model value")
            model = values[index + 1]
            if model != LUNA_MODEL:
                raise ModelPolicyError(f"non-Luna model requested: {model}")
        elif token.startswith("--model="):
            model = token.split("=", 1)[1]
            if model != LUNA_MODEL:
                raise ModelPolicyError(f"non-Luna model requested: {model}")
        elif token in {"-c", "--config"} and index + 1 < len(values):
            key, raw = _config_value(values[index + 1])
            if key == "model" and raw != LUNA_MODEL:
                raise ModelPolicyError(f"non-Luna config model requested: {raw}")
            if key == "model_reasoning_effort" and raw != REASONING_EFFORT:
                raise ModelPolicyError(f"non-medium reasoning effort requested: {raw}")
        elif token.startswith("--config="):
            key, raw = _config_value(token.split("=", 1)[1])
            if key == "model" and raw != LUNA_MODEL:
                raise ModelPolicyError(f"non-Luna config model requested: {raw}")
            if key == "model_reasoning_effort" and raw != REASONING_EFFORT:
                raise ModelPolicyError(f"non-medium reasoning effort requested: {raw}")


def _ensure_flags(command: Sequence[str], insertion_index: int) -> List[str]:
    values = list(command)
    _validate_explicit_flags(values)

    model_present = any(
        token in {"-m", "--model"} or token.startswith("--model=")
        for token in values
    )
    effort_present = any(
        (
            token in {"-c", "--config"}
            and index + 1 < len(values)
            and _config_value(values[index + 1])[0] == "model_reasoning_effort"
        )
        or (
            token.startswith("--config=")
            and _config_value(token.split("=", 1)[1])[0] == "model_reasoning_effort"
        )
        for index, token in enumerate(values)
    )
    additions: List[str] = []
    if not model_present:
        additions.extend(["-m", LUNA_MODEL])
    if not effort_present:
        additions.extend(["-c", 'model_reasoning_effort="medium"'])
    if additions:
        values[insertion_index:insertion_index] = additions
    return values


def ensure_luna_app_server_command(command: Sequence[str]) -> List[str]:
    """Pin a Codex App Server command to Luna and medium effort.

    Non-Codex test doubles are preserved so protocol unit tests remain
    deterministic.  A real Codex App Server command is always made explicit.
    """

    values = list(command)
    if not values or Path(values[0]).name != "codex" or "app-server" not in values:
        return values
    return _ensure_flags(values, 1)


def ensure_luna_exec_command(command: Sequence[str]) -> List[str]:
    """Pin a direct ``codex exec`` or ``codex exec resume`` command."""

    values = list(command)
    if not values or Path(values[0]).name != "codex" or "exec" not in values:
        return values
    insertion_index = values.index("exec") + 1
    return _ensure_flags(values, insertion_index)


def luna_thread_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """Return explicit thread parameters for a Luna-only experiment."""

    result = dict(params)
    requested = result.get("model")
    if requested is not None and requested != LUNA_MODEL:
        raise ModelPolicyError(f"thread requested non-Luna model: {requested}")
    result["model"] = LUNA_MODEL
    # Approval review must remain user-owned; auto_review/guardian_subagent
    # could introduce a separately selected model into the control path.
    result.setdefault("approvalsReviewer", "user")
    # The installed schema accepts this custom mode hint.  Runtime events are
    # still checked for any actual sub-agent activity.
    result.setdefault("multiAgentMode", {"custom": "disabled"})
    return result


def luna_turn_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """Return explicit turn parameters for a Luna-only experiment."""

    result = dict(params)
    requested = result.get("model")
    if requested is not None and requested != LUNA_MODEL:
        raise ModelPolicyError(f"turn requested non-Luna model: {requested}")
    effort = result.get("effort")
    if effort is not None and effort != REASONING_EFFORT:
        raise ModelPolicyError(f"turn requested non-medium effort: {effort}")
    result["model"] = LUNA_MODEL
    result["effort"] = REASONING_EFFORT
    result.setdefault("approvalsReviewer", "user")
    result.setdefault("multiAgentMode", {"custom": "disabled"})
    return result


def _model_values(value: Any, *, key_path: str = "") -> List[str]:
    values: List[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{key_path}.{key}" if key_path else str(key)
            lowered = str(key).lower()
            if lowered in {"model", "modelid", "model_id"} and isinstance(item, str):
                values.append(item)
            elif lowered not in {"prompt", "text", "input", "developerinstructions"}:
                values.extend(_model_values(item, key_path=path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            values.extend(_model_values(item, key_path=f"{key_path}[{index}]"))
    return values


def first_model(*values: Any) -> Optional[str]:
    """Extract the first explicit model value from response/event payloads."""

    for value in values:
        models = _model_values(value)
        if models:
            return models[0]
    return None


def models_from_events(events: Iterable[Mapping[str, Any]]) -> List[str]:
    values: List[str] = []
    for event in events:
        values.extend(_model_values(event))
    return values


def parse_jsonl_events(text: str) -> List[Dict[str, Any]]:
    """Parse only JSON objects from a ``codex exec --json`` stream."""

    events: List[Dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def run_luna_exec_attestation(
    command: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: float = 120.0,
) -> Tuple[subprocess.CompletedProcess[str], List[Dict[str, Any]], ModelAttestation]:
    """Run an explicit no-tool CLI turn and attest its JSONL telemetry.

    Callers must pass a harmless no-tool prompt. The command is pinned before
    launch and a nonzero process exit is treated as a failed model attempt.
    This helper never retries with another model.
    """

    pinned = ensure_luna_exec_command(command)
    completed = subprocess.run(
        pinned,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    events = parse_jsonl_events(completed.stdout)
    initial = attest_exec_events(events)
    if completed.returncode == 0:
        return completed, events, initial
    failed = attest_model(
        requested_model=initial.requested_model,
        runtime_reported_model=initial.runtime_reported_model,
        thread_model=initial.thread_model,
        turn_model=initial.turn_model,
        reasoning_effort=initial.reasoning_effort,
        fallback_detected=True,
        non_luna_model_calls=initial.non_luna_model_calls,
        auto_review_model_override=initial.auto_review_model_override,
        review_model=initial.review_model,
        delegated_model=initial.delegated_model,
        subagent_calls=initial.subagent_calls,
    )
    return completed, events, failed


def _effort_values(value: Any) -> List[str]:
    values: List[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"effort", "reasoning_effort", "reasoningeffort"} and isinstance(item, str):
                values.append(item)
            else:
                values.extend(_effort_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_effort_values(item))
    return values


def _event_values(events: Iterable[Mapping[str, Any]], type_terms: Sequence[str]) -> List[Mapping[str, Any]]:
    result: List[Mapping[str, Any]] = []
    for event in events:
        event_type = str(event.get("type") or event.get("method") or "").lower()
        if any(term in event_type for term in type_terms):
            result.append(event)
    return result


def attest_exec_events(events: Iterable[Mapping[str, Any]]) -> ModelAttestation:
    """Attest JSONL ``codex exec`` telemetry without trusting configuration."""

    values = list(events)
    thread_events = _event_values(values, ("thread.started", "thread_started"))
    turn_events = _event_values(values, ("turn.started", "turn_started", "turn.completed", "turn_completed"))
    runtime_model = first_model(*values)
    thread_model = first_model(*thread_events)
    turn_model = first_model(*turn_events)
    effort_values = _effort_values(turn_events)
    effort = effort_values[0] if effort_values else None
    models = models_from_events(values)
    non_luna = sum(1 for model in models if model != LUNA_MODEL)
    delegation = subagent_events(values)
    fallback_detected = any(
        any(term in json.dumps(event, ensure_ascii=False).lower() for term in (
            "model.rerouted",
            "model_rerouted",
            "model fallback",
            "fallback_model",
            "fallbackmodel",
        ))
        for event in values
    )
    return attest_model(
        requested_model=LUNA_MODEL,
        runtime_reported_model=runtime_model,
        thread_model=thread_model,
        turn_model=turn_model,
        reasoning_effort=effort or REASONING_EFFORT,
        fallback_detected=fallback_detected,
        non_luna_model_calls=non_luna,
        subagent_calls=[item.get("type") or item.get("method") or "unknown" for item in delegation],
    )


def subagent_events(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return events that indicate delegation or sub-agent activity."""

    matches: List[Dict[str, Any]] = []
    for event in events:
        text = json.dumps(event, ensure_ascii=False).lower()
        if any(term in text for term in ("spawnagent", "subagent", "sub_agent", "agentcontrol")):
            matches.append(dict(event))
    return matches


@dataclass(frozen=True)
class ModelAttestation:
    requested_model: Optional[str]
    runtime_reported_model: Optional[str]
    thread_model: Optional[str]
    turn_model: Optional[str]
    reasoning_effort: Optional[str]
    fallback_detected: bool = False
    non_luna_model_calls: int = 0
    auto_review_model_override: Optional[str] = None
    review_model: Optional[str] = None
    delegated_model: Optional[str] = None
    subagent_calls: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        return not self.reasons

    @property
    def status(self) -> str:
        return "PASS" if self.verified else "FAIL"

    @property
    def observed_model(self) -> Optional[str]:
        return self.turn_model or self.runtime_reported_model or self.thread_model

    def as_dict(self, *, experiment_valid: Optional[bool] = None) -> Dict[str, Any]:
        valid = self.verified if experiment_valid is None else bool(experiment_valid and self.verified)
        return {
            "model_policy": MODEL_POLICY,
            "requested_model": self.requested_model,
            "runtime_reported_model": self.runtime_reported_model,
            "thread_model": self.thread_model,
            "turn_model": self.turn_model,
            "observed_model": self.observed_model,
            "reasoning_effort": self.reasoning_effort,
            "model_attestation": self.status,
            "experiment_valid": valid,
            "fallback_detected": self.fallback_detected,
            "auto_review_model_override": self.auto_review_model_override,
            "review_model": self.review_model,
            "delegated_model": self.delegated_model,
            "subagent_policy": SUBAGENT_POLICY,
            "subagent_calls": list(self.subagent_calls),
            "non_luna_model_calls": self.non_luna_model_calls,
            "attestation_reasons": list(self.reasons),
        }


def attest_model(
    *,
    requested_model: Any,
    runtime_reported_model: Any,
    thread_model: Any,
    turn_model: Any,
    reasoning_effort: Any,
    fallback_detected: bool = False,
    non_luna_model_calls: int = 0,
    auto_review_model_override: Any = None,
    review_model: Any = None,
    delegated_model: Any = None,
    subagent_calls: Iterable[Any] = (),
) -> ModelAttestation:
    """Build strict runtime attestation; every required value is mandatory."""

    requested = _string(requested_model)
    runtime = _string(runtime_reported_model)
    thread = _string(thread_model)
    turn = _string(turn_model)
    effort = _string(reasoning_effort)
    auto_review = _string(auto_review_model_override)
    review = _string(review_model)
    delegated = _string(delegated_model)
    subagents = tuple(_string(value) or "<unknown>" for value in subagent_calls)
    reasons: List[str] = []

    for name, value in (
        ("requested_model", requested),
        ("runtime_reported_model", runtime),
        ("thread_model", thread),
        ("turn_model", turn),
        ("reasoning_effort", effort),
    ):
        if value is None:
            reasons.append(f"missing_{name}")
    if requested is not None and requested != LUNA_MODEL:
        reasons.append("requested_model_not_luna")
    for name, value in (
        ("runtime_reported_model", runtime),
        ("thread_model", thread),
        ("turn_model", turn),
    ):
        if value is not None and value != LUNA_MODEL:
            reasons.append(f"{name}_not_luna")
    if effort is not None and effort != REASONING_EFFORT:
        reasons.append("reasoning_effort_not_medium")
    if fallback_detected:
        reasons.append("automatic_fallback_detected")
    if non_luna_model_calls:
        reasons.append("non_luna_model_call_detected")
    if auto_review is not None:
        reasons.append("auto_review_model_override_present")
    if review is not None and review != "user":
        reasons.append("non_user_review_model_present")
    if delegated is not None:
        reasons.append("delegated_model_present")
    if subagents:
        reasons.append("subagent_activity_detected")

    return ModelAttestation(
        requested_model=requested,
        runtime_reported_model=runtime,
        thread_model=thread,
        turn_model=turn,
        reasoning_effort=effort,
        fallback_detected=bool(fallback_detected),
        non_luna_model_calls=int(non_luna_model_calls),
        auto_review_model_override=auto_review,
        review_model=review,
        delegated_model=delegated,
        subagent_calls=subagents,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def require_attestation(attestation: ModelAttestation) -> ModelAttestation:
    if not attestation.verified:
        raise ModelPolicyError("MODEL_ATTESTATION=FAIL: " + ",".join(attestation.reasons))
    return attestation


def emit_model_policy_log(attestation: ModelAttestation) -> None:
    """Emit the mandatory pre-probe log without hiding missing telemetry."""

    print(f"MODEL POLICY: {MODEL_POLICY}")
    print(f"REQUESTED MODEL: {attestation.requested_model or '<missing>'}")
    print(f"OBSERVED MODEL: {attestation.observed_model or '<missing>'}")
    print(f"REASONING EFFORT: {attestation.reasoning_effort or '<missing>'}")
    print(f"MODEL ATTESTATION: {attestation.status}")


def write_model_attestation(
    path: Path,
    attestation: ModelAttestation,
    *,
    experiment: str,
    experiment_valid: Optional[bool] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist one attestation record, retaining prior experiment records."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = attestation.as_dict(experiment_valid=experiment_valid)
    record["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record["experiment"] = experiment
    if extra:
        record.update(dict(extra))
    previous: Dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, ValueError):
            previous = {}
    experiments = list(previous.get("experiments", []))
    experiments.append(record)
    output = {
        "schema_version": 1,
        "generated_at": record.get("generated_at"),
        **{key: value for key, value in record.items() if key != "experiment"},
        "experiments": experiments,
    }
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def attach_model_metadata(value: Mapping[str, Any], attestation: ModelAttestation, *, experiment_valid: Optional[bool] = None) -> Dict[str, Any]:
    result = dict(value)
    result.update(attestation.as_dict(experiment_valid=experiment_valid))
    return result


__all__ = [
    "LUNA_MODEL",
    "MODEL_POLICY",
    "ModelAttestation",
    "ModelPolicyError",
    "REASONING_EFFORT",
    "SUBAGENT_POLICY",
    "attach_model_metadata",
    "attest_exec_events",
    "attest_model",
    "ensure_luna_app_server_command",
    "ensure_luna_exec_command",
    "emit_model_policy_log",
    "first_model",
    "luna_thread_params",
    "luna_turn_params",
    "models_from_events",
    "parse_jsonl_events",
    "require_attestation",
    "run_luna_exec_attestation",
    "subagent_events",
    "write_model_attestation",
]

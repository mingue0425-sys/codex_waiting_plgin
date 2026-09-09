from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class ExecutionState(str, Enum):
    SUBMITTED = "SUBMITTED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LOST = "LOST"
    ORPHANED = "ORPHANED"
    COMPLETED_STALE = "COMPLETED_STALE"


class DeliveryState(str, Enum):
    NOT_READY = "NOT_READY"
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SENDING = "SENDING"
    SENT_UNCONFIRMED = "SENT_UNCONFIRMED"
    ACKED = "ACKED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"


class LoggingState(str, Enum):
    OK = "OK"
    TRUNCATED = "TRUNCATED"
    FAILED = "FAILED"


class JobError(RuntimeError):
    """An expected job lifecycle error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class JobSpec:
    schema_version: int
    job_id: str
    command: str
    cwd: str
    shell: str

    @classmethod
    def create(cls, command: str, cwd: Path, shell: str) -> "JobSpec":
        return cls(
            schema_version=1,
            job_id=str(uuid.uuid4()),
            command=command,
            cwd=str(cwd),
            shell=str(Path(shell)),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.as_dict())

    def digest(self) -> str:
        return sha256_bytes(self.canonical_bytes())


@dataclass
class ProcessIdentity:
    pid: int
    pgid: Optional[int]
    start_time: Optional[str]
    executable: Optional[str]
    command_fingerprint: Optional[str]


@dataclass
class LogLimits:
    max_log_bytes: int = 256 * 1024 * 1024
    max_preview_bytes: int = 64 * 1024
    max_preview_lines: int = 200
    max_single_line_bytes: int = 8192

    def __post_init__(self) -> None:
        for name in (
            "max_log_bytes",
            "max_preview_bytes",
            "max_preview_lines",
            "max_single_line_bytes",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass
class LogInfo:
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    combined_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    combined_truncated: bool = False
    preview_truncated: bool = False
    state: LoggingState = LoggingState.OK

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


@dataclass
class Metadata:
    job_id: str
    spec_sha256: str
    command: str
    cwd: str
    shell: str
    execution_state: ExecutionState
    result_state: Optional[str]
    supervisor_pid: Optional[int]
    pid: Optional[int]
    process_group: Optional[int]
    start_time: Optional[str]
    finish_time: Optional[str]
    exit_code: Optional[int]
    termination_signal: Optional[int]
    process_identity: Optional[ProcessIdentity]
    project_fingerprint_start: Optional[Dict[str, Any]]
    project_fingerprint_end: Optional[Dict[str, Any]]
    stale: Optional[bool]
    logging: LogInfo
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    cancellation_requested_at: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["execution_state"] = self.execution_state.value
        value["logging"] = self.logging.as_dict()
        if self.process_identity is not None:
            value["process_identity"] = asdict(self.process_identity)
        return value


@dataclass
class Result:
    schema_version: int
    job_id: str
    spec_sha256: str
    execution_state: ExecutionState
    result_state: str
    exit_code: Optional[int]
    termination_signal: Optional[int]
    started_at: Optional[str]
    completed_at: Optional[str]
    duration_seconds: Optional[float]
    process_identity: Optional[ProcessIdentity]
    project_fingerprint_start: Optional[Dict[str, Any]]
    project_fingerprint_end: Optional[Dict[str, Any]]
    source_state_changed: Optional[bool]
    logging: LogInfo
    log_preview: str
    completion_event_id: str
    result_sha256: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["execution_state"] = self.execution_state.value
        value["logging"] = self.logging.as_dict()
        if self.process_identity is not None:
            value["process_identity"] = asdict(self.process_identity)
        return value


@dataclass
class Delivery:
    schema_version: int
    job_id: str
    completion_event_id: Optional[str]
    result_sha256: Optional[str]
    state: DeliveryState
    attempts: int = 0
    last_attempt_at: Optional[str] = None
    acked_at: Optional[str] = None
    transport: Optional[str] = None
    remote_marker: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


def process_exit_code(returncode: int) -> tuple[Optional[int], Optional[int]]:
    if returncode >= 0:
        return returncode, None
    return None, -returncode


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def deserialize_spec(value: Dict[str, Any]) -> JobSpec:
    return JobSpec(
        schema_version=int(value["schema_version"]),
        job_id=str(value["job_id"]),
        command=str(value["command"]),
        cwd=str(value["cwd"]),
        shell=str(value["shell"]),
    )


def env_name_hashes(environment: Dict[str, str]) -> list[str]:
    """Return stable names-only evidence without persisting environment values."""
    return sorted(sha256_bytes(name.encode("utf-8")) for name in environment)


def result_digest(value: Dict[str, Any]) -> str:
    """Digest a result while excluding its self-referential digest field."""
    unsigned = dict(value)
    unsigned["result_sha256"] = None
    return sha256_bytes(canonical_json(unsigned))


def result_hash_matches(value: Dict[str, Any]) -> bool:
    expected = value.get("result_sha256")
    return isinstance(expected, str) and expected == result_digest(value)

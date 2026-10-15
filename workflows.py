from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

ROOT = Path(__file__).resolve().parent
type Sha = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{40}$")]


class Data(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)


class CheckRun(Data):
    kind: Literal["CheckRun"] = Field(alias="__typename")
    name: str
    status: Literal["QUEUED", "IN_PROGRESS", "COMPLETED", "WAITING", "REQUESTED", "PENDING"]
    conclusion: (
        Literal[
            "",
            "ACTION_REQUIRED",
            "CANCELLED",
            "FAILURE",
            "NEUTRAL",
            "SKIPPED",
            "STALE",
            "STARTUP_FAILURE",
            "SUCCESS",
            "TIMED_OUT",
        ]
        | None
    )
    detailsUrl: str


class StatusContext(Data):
    kind: Literal["StatusContext"] = Field(alias="__typename")
    context: str
    state: Literal["SUCCESS", "FAILURE", "ERROR", "PENDING", "EXPECTED"]
    targetUrl: str | None


class PullRequest(Data):
    number: Annotated[int, Field(gt=0)]
    url: Annotated[
        str, StringConstraints(pattern=r"^https://[^/\s]+/[^/\s]+/[^/\s]+/pull/[1-9][0-9]*$")
    ]
    title: str
    body: str
    headRefOid: Sha
    baseRefOid: Sha
    statusCheckRollup: tuple[CheckRun | StatusContext, ...] | None


class Job(Data):
    databaseId: Annotated[int, Field(gt=0)]


class Run(Data):
    headSha: Sha
    jobs: tuple[Job, ...]


class State(StrEnum):
    FAILED = "FAILED"
    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    SKIPPED = "SKIPPED"
    NEUTRAL = "NEUTRAL"
    CANCELED = "CANCELED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Check:
    name: str
    state: State
    url: str


def checks(pr: PullRequest) -> tuple[Check, ...]:
    result: list[Check] = []
    for item in pr.statusCheckRollup or ():
        if isinstance(item, CheckRun):
            if item.status != "COMPLETED":
                state = State.PENDING
            else:
                match item.conclusion:
                    case "SUCCESS":
                        state = State.SUCCESSFUL
                    case "SKIPPED":
                        state = State.SKIPPED
                    case "NEUTRAL":
                        state = State.NEUTRAL
                    case "CANCELLED":
                        state = State.CANCELED
                    case "ACTION_REQUIRED" | "FAILURE" | "STALE" | "STARTUP_FAILURE" | "TIMED_OUT":
                        state = State.FAILED
                    case "" | None:
                        state = State.UNAVAILABLE
            result.append(Check(item.name, state, item.detailsUrl))
        else:
            match item.state:
                case "SUCCESS":
                    state = State.SUCCESSFUL
                case "FAILURE" | "ERROR":
                    state = State.FAILED
                case "PENDING" | "EXPECTED":
                    state = State.PENDING
            result.append(Check(item.context, state, item.targetUrl or ""))
    return tuple(result)


def overall(items: tuple[Check, ...]) -> State:
    states = {item.state for item in items}
    for state in (State.FAILED, State.PENDING, State.UNAVAILABLE, State.CANCELED):
        if state in states:
            return state
    return State.SUCCESSFUL if State.SUCCESSFUL in states else State.UNAVAILABLE


@dataclass(frozen=True)
class Captured:
    text: str


@dataclass(frozen=True)
class Failed:
    command: str
    status: int
    detail: str


def capture(arguments: list[str]) -> Captured | Failed:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return Failed(arguments[0], 69, "missing dependency")
    if result.returncode:
        return Failed(arguments[0], result.returncode, result.stderr[-4000:])
    return Captured(result.stdout)


def log_evidence(pr: PullRequest, item: Check, directory: Path, index: int) -> str:
    pr_url = urlparse(pr.url)
    link = urlparse(item.url)
    match = re.fullmatch(r"/([^/]+/[^/]+)/actions/runs/([0-9]+)/job/([0-9]+)", link.path)
    if (
        match is None
        or link.hostname != pr_url.hostname
        or not pr_url.path.startswith(f"/{match[1]}/pull/")
    ):
        return "Logs unavailable: no GitHub Actions job link."
    repo, run, job = match.groups()
    repo = f"{pr_url.hostname}/{repo}"
    identity = capture(["gh", "run", "view", run, "--repo", repo, "--json", "headSha,jobs"])
    if isinstance(identity, Failed):
        return "Logs unavailable: run metadata request failed."
    try:
        run_data = Run.model_validate_json(identity.text)
    except ValidationError:
        return "Logs unavailable: invalid run metadata."
    if run_data.headSha != pr.headRefOid:
        return "Logs unavailable: run head differs from the PR head."
    if not any(str(member.databaseId) == job for member in run_data.jobs):
        return "Logs unavailable: job does not belong to the run."
    _ = (directory / f"run-{index}.json").write_text(identity.text, encoding="utf-8")
    logs = capture(["gh", "run", "view", run, "--repo", repo, "--job", job, "--log-failed"])
    if isinstance(logs, Failed) or not logs.text.strip():
        return "Logs unavailable: GitHub did not return failure logs."
    _ = (directory / f"job-{index}.log").write_text(logs.text, encoding="utf-8")
    # Keep the full local log. Bound the excerpt sent to Pi and the terminal.
    lines = logs.text.splitlines()
    errors = [
        line for line in lines if re.search(r"error|fail|exception|fatal", line, re.IGNORECASE)
    ]
    excerpt = "\n".join(errors[:40] + lines[-30:])[:24000]
    return f"Evidence: job-{index}.log\n\n```text\n{excerpt}\n```"


def reason(mode: str, directory: Path, attachments: list[Path]) -> Captured | Failed:
    result = capture(
        [
            "pi",
            "--print",
            "--no-session",
            "--no-tools",
            "--no-mcp",
            "--no-extensions",
            "--no-skills",
            "--no-context-files",
            "--no-prompt-templates",
            "--offline",
            "--append-system-prompt",
            str(ROOT / f"prompts/{mode}.md"),
            "--",
            *(f"@{path}" for path in attachments),
            f"Produce the local {mode} report from these attachments. Artifacts: {directory}",
        ],
    )
    if isinstance(result, Captured) and not result.text.strip():
        return Failed("pi", 69, "empty response")
    return result


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Local PR review and read-only CI diagnosis")
    _ = parser.add_argument("mode", choices=("review", "ci"))
    _ = parser.add_argument("pr")
    _ = parser.add_argument("--repo", help="[HOST/]OWNER/REPO, or use the current Git repository")
    args = parser.parse_args(arguments)
    mode: str = args.mode
    selector: str = args.pr
    repo: str | None = args.repo
    if (
        re.fullmatch(r"[1-9][0-9]*|https://[^/\s]+/[^/\s]+/[^/\s]+/pull/[1-9][0-9]*", selector)
        is None
    ):
        print("ai: PR must be a positive number or an HTTPS PR URL", file=sys.stderr)
        return 64
    if (
        repo is not None
        and re.fullmatch(r"(?:[a-zA-Z0-9.-]+/)?[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repo) is None
    ):
        print("ai: invalid repository", file=sys.stderr)
        return 64
    scope = ["--repo", repo] if repo is not None else []
    fields = "number,url,title,body,headRefOid,baseRefOid,statusCheckRollup"
    metadata = capture(["gh", "pr", "view", selector, *scope, "--json", fields])
    if isinstance(metadata, Failed):
        print(f"ai: GitHub data unavailable: {metadata.detail}", file=sys.stderr)
        return 69
    try:
        pr = PullRequest.model_validate_json(metadata.text)
    except ValidationError as error:
        print(
            f"ai: GitHub data unavailable: invalid metadata ({error.error_count()} errors)",
            file=sys.stderr,
        )
        return 69
    reports = (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "ai/reports"
    )
    reports.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(
        tempfile.mkdtemp(prefix=f"{mode}-{pr.number}-{pr.headRefOid[:12]}-", dir=reports)
    )
    metadata_path = directory / "pr.json"
    _ = metadata_path.write_text(metadata.text, encoding="utf-8")
    header = f"# PR {mode}: {pr.title}\n\nPR: {pr.url}\n\nHead: `{pr.headRefOid}`\n\n"
    attachments = [metadata_path]
    if mode == "review":
        diff = capture(["gh", "pr", "diff", selector, *scope, "--color", "never"])
        if isinstance(diff, Failed):
            print(f"ai: diff unavailable. Evidence: {directory}", file=sys.stderr)
            return 69
        diff_path = directory / "diff.patch"
        _ = diff_path.write_text(diff.text, encoding="utf-8")
        if len(diff.text.encode()) > 1_000_000:
            print(f"ai: diff exceeds 1 MB. Evidence: {directory}", file=sys.stderr)
            return 65
        attachments.append(diff_path)
        status = 0
        evidence = ""
    else:
        items = checks(pr)
        state = overall(items)
        status = {
            State.SUCCESSFUL: 0,
            State.FAILED: 1,
            State.PENDING: 8,
            State.UNAVAILABLE: 69,
            State.CANCELED: 69,
            State.SKIPPED: 69,
            State.NEUTRAL: 69,
        }[state]
        evidence = f"Status: {state.value}\n\n"
        if not items:
            evidence += "Checks unavailable: GitHub returned no check records.\n\n"
        for index, item in enumerate(items):
            evidence += f"- {item.name}: {item.state.value} {item.url}\n"
            if item.state == State.FAILED:
                evidence += f"\n{log_evidence(pr, item, directory, index)}\n\n"
        evidence_path = directory / "checks.md"
        _ = evidence_path.write_text(header + evidence, encoding="utf-8")
        attachments.append(evidence_path)
    # Re-read the PR after the separate diff/log requests to reject head movement.
    current = capture(["gh", "pr", "view", pr.url, "--json", fields])
    try:
        same_revision = isinstance(current, Captured) and (
            (latest := PullRequest.model_validate_json(current.text)).url,
            latest.headRefOid,
            latest.baseRefOid,
        ) == (pr.url, pr.headRefOid, pr.baseRefOid)
    except ValidationError:
        same_revision = False
    if not same_revision:
        print(
            f"ai: PR head changed or became unavailable. Retry. Evidence: {directory}",
            file=sys.stderr,
        )
        return 75
    analysis: Captured | Failed = Captured("")
    if mode == "review" or status == 1:
        analysis = reason(mode, directory, attachments)
    if isinstance(analysis, Failed):
        evidence += "\nPi diagnosis unavailable. Native evidence remains in this report.\n"
        print(f"ai: Pi failed: {analysis.detail}", file=sys.stderr)
        status = 69
        analysis = Captured("")
    report = directory / "report.md"
    text = header + evidence + "\n" + analysis.text
    _ = report.write_text(text, encoding="utf-8")
    print(text)
    print(f"Report: {report}")
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except OSError as error:
        print(f"ai: local operation failed: {error.strerror}", file=sys.stderr)
        raise SystemExit(74) from error
    except KeyboardInterrupt:
        raise SystemExit(130) from None

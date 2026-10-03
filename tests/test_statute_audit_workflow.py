from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "statute-snapshot-audit.yml"


# ---------------------------------------------------------------------------
# Workflow structure
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def steps_of(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    return workflow["jobs"][job]["steps"]


def step_named(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    matches = [step for step in steps_of(workflow, job) if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_workflow_triggers_on_schedule_and_dispatch(workflow) -> None:
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] and triggers["schedule"][0]["cron"]
    assert "workflow_dispatch" in triggers


def test_workflow_branches_on_event_when_secret_is_missing(workflow) -> None:
    check = step_named(workflow, "credential", "Check credential")
    script = check["run"]
    assert "LAW_OPEN_API_OC" in script
    assert 'GITHUB_EVENT_NAME" = "workflow_dispatch"' in script
    assert "::error" in script and "exit 1" in script
    assert "available=true" in script and "available=false" in script

    issue = step_named(workflow, "credential", "Open or update the blocked-audit issue")
    assert "github.event_name == 'schedule'" in issue["if"]
    assert "steps.check.outputs.available == 'false'" in issue["if"]
    assert "gh issue create" in issue["run"] and "gh issue edit" in issue["run"]
    # One issue edited in place; never a daily comment.
    assert "gh issue comment" not in issue["run"]
    assert "마지막 확인(UTC)" in issue["run"]
    assert workflow["env"]["BLOCKED_ISSUE_LABEL"] == "statute-audit-blocked"


def test_refresh_runs_only_with_credential_and_no_longer_hard_fails(workflow) -> None:
    refresh = workflow["jobs"]["refresh"]
    assert refresh["needs"] == "credential"
    assert refresh["if"] == "needs.credential.outputs.available == 'true'"
    assert 'test -n "$LAW_OPEN_API_OC"' not in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert workflow["jobs"]["credential"]["outputs"]["available"]


def test_resolve_job_closes_issue_only_after_successful_refresh(workflow) -> None:
    resolve = workflow["jobs"]["resolve"]
    assert set(resolve["needs"]) == {"credential", "refresh"}
    assert resolve["if"] == "needs.refresh.result == 'success'"
    script = steps_of(workflow, "resolve")[0]["run"]
    assert "gh issue comment" in script and "gh issue close" in script


def test_workflow_permissions_are_least_privilege(workflow) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["credential"]["permissions"] == {"issues": "write"}
    assert workflow["jobs"]["refresh"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert workflow["jobs"]["resolve"]["permissions"] == {"issues": "write"}


def test_refresh_job_publishes_both_groups_in_pr_body(workflow) -> None:
    refresh_steps = steps_of(workflow, "refresh")
    audit = step_named(workflow, "refresh", "Refresh official statute snapshots")
    assert "--pr-body" in audit["run"] and "--report reports/statute_audit.json" in audit["run"]
    pr_step = next(step for step in refresh_steps if "create-pull-request" in step.get("uses", ""))
    assert pr_step["with"]["body-path"].endswith("statute-audit-pr-body.md")
    assert "body" not in pr_step["with"]
    assert pr_step["if"] == "steps.audit.outputs.changed == 'true'"
    assert pr_step["with"]["labels"] == "statute-update"
    # Unchanged days must not open a PR for a bumped checked_at alone.
    for name in ("Refresh static web data", "Verify"):
        assert step_named(workflow, "refresh", name)["if"] == pr_step["if"]


def test_workflow_actions_are_pinned_to_major_versions(workflow) -> None:
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", step["uses"]), step["uses"]


# ---------------------------------------------------------------------------
# Workflow shell logic against a fake `gh` (POSIX only)
# ---------------------------------------------------------------------------

FAKE_GH = """#!{python}
import json, os, sys
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]
state["calls"].append(args)
def flag(name):
    return args[args.index(name) + 1] if name in args else None
command = " ".join(args[:2])
if command == "label create":
    pass
elif command == "issue list":
    print(json.dumps([{{"number": i["number"], "title": i["title"]}}
                      for i in state["issues"]
                      if i["state"] == "open" and flag("--label") in i["labels"]]))
elif command == "issue create":
    number = max([i["number"] for i in state["issues"]] + [0]) + 1
    state["issues"].append({{"number": number, "title": flag("--title"),
        "labels": [flag("--label")], "state": "open",
        "body": open(flag("--body-file"), encoding="utf-8").read(), "comments": []}})
elif command == "issue edit":
    issue = next(i for i in state["issues"] if str(i["number"]) == args[2])
    issue["body"] = open(flag("--body-file"), encoding="utf-8").read()
elif command == "issue comment":
    issue = next(i for i in state["issues"] if str(i["number"]) == args[2])
    issue["comments"].append(flag("--body"))
elif command == "issue close":
    issue = next(i for i in state["issues"] if str(i["number"]) == args[2])
    issue["state"] = "closed"
else:
    sys.exit("unexpected gh call: " + " ".join(args))
json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False)
"""

posix_only = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not shutil.which("jq"),
    reason="needs bash and jq",
)


class Harness:
    def __init__(self, tmp_path: Path, workflow: dict[str, Any]) -> None:
        self.tmp_path = tmp_path
        self.workflow = workflow
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.bin_dir = bin_dir
        self.state_path = tmp_path / "gh-state.json"
        self.output_path = tmp_path / "github-output"
        self.state: dict[str, Any] = {"issues": [], "calls": []}
        self._save()

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def seed_issue(self, title: str, *, label: str, number: int | None = None) -> None:
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state["issues"].append(
            {
                "number": number or len(self.state["issues"]) + 1,
                "title": title,
                "labels": [label],
                "state": "open",
                "body": "old",
                "comments": [],
            }
        )
        self._save()

    def run(self, job: str, step_name: str, *, event: str, secret: str = "") -> subprocess.CompletedProcess:
        step = step_named(self.workflow, job, step_name)
        env = {
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "FAKE_GH_STATE": str(self.state_path),
            "GITHUB_OUTPUT": str(self.output_path),
            "GITHUB_EVENT_NAME": event,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "42",
            "RUNNER_TEMP": str(self.tmp_path),
            "LAW_OPEN_API_OC": secret,
            "BLOCKED_ISSUE_TITLE": self.workflow["env"]["BLOCKED_ISSUE_TITLE"],
            "BLOCKED_ISSUE_LABEL": self.workflow["env"]["BLOCKED_ISSUE_LABEL"],
        }
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def snapshot(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def output(self) -> str:
        return self.output_path.read_text(encoding="utf-8") if self.output_path.exists() else ""

    def gh_calls(self) -> list[str]:
        return [" ".join(call[:2]) for call in self.snapshot()["calls"]]


@pytest.fixture
def harness(tmp_path: Path, workflow) -> Harness:
    return Harness(tmp_path, workflow)


CHECK = "Check credential"
ISSUE = "Open or update the blocked-audit issue"


@posix_only
def test_secret_present_proceeds_without_touching_issues(harness: Harness) -> None:
    completed = harness.run("credential", CHECK, event="schedule", secret="my-oc")

    assert completed.returncode == 0, completed.stderr
    assert "available=true" in harness.output()
    assert harness.gh_calls() == []


@posix_only
def test_missing_secret_on_schedule_succeeds_and_flags_unavailable(harness: Harness) -> None:
    completed = harness.run("credential", CHECK, event="schedule")

    assert completed.returncode == 0, completed.stderr
    assert "available=false" in harness.output()
    assert "::warning" in completed.stdout


@posix_only
def test_whitespace_secret_counts_as_missing(harness: Harness) -> None:
    completed = harness.run("credential", CHECK, event="schedule", secret="  \t ")

    assert completed.returncode == 0
    assert "available=false" in harness.output()


@posix_only
def test_missing_secret_on_dispatch_fails_loudly(harness: Harness) -> None:
    completed = harness.run("credential", CHECK, event="workflow_dispatch")

    assert completed.returncode == 1
    assert "::error" in completed.stdout
    assert "Settings > Secrets and variables > Actions" in completed.stdout


@posix_only
def test_blocked_issue_is_created_once_then_edited_in_place(harness: Harness) -> None:
    first = harness.run("credential", ISSUE, event="schedule")
    second = harness.run("credential", ISSUE, event="schedule")
    third = harness.run("credential", ISSUE, event="schedule")

    for completed in (first, second, third):
        assert completed.returncode == 0, completed.stderr
    state = harness.snapshot()
    assert len(state["issues"]) == 1
    issue = state["issues"][0]
    assert issue["labels"] == ["statute-audit-blocked"]
    assert issue["comments"] == []
    assert "마지막 확인(UTC): 20" in issue["body"]
    assert "https://github.com/owner/repo/actions/runs/42" in issue["body"]
    assert "__CHECKED_AT__" not in issue["body"] and "__RUN_URL__" not in issue["body"]
    assert "LAW_OPEN_API_OC" in issue["body"]
    calls = harness.gh_calls()
    assert calls.count("issue create") == 1
    assert calls.count("issue edit") == 2
    assert "issue comment" not in calls


@posix_only
def test_existing_duplicates_edit_the_oldest_and_unrelated_issues_are_untouched(
    harness: Harness, workflow
) -> None:
    title = workflow["env"]["BLOCKED_ISSUE_TITLE"]
    label = workflow["env"]["BLOCKED_ISSUE_LABEL"]
    harness.seed_issue("Some other blocked thing", label=label, number=3)
    harness.seed_issue(title, label=label, number=7)
    harness.seed_issue(title, label=label, number=5)

    completed = harness.run("credential", ISSUE, event="schedule")

    assert completed.returncode == 0, completed.stderr
    issues = {issue["number"]: issue for issue in harness.snapshot()["issues"]}
    assert len(issues) == 3
    assert issues[5]["body"] != "old"
    assert issues[7]["body"] == "old" and issues[3]["body"] == "old"


@posix_only
def test_resolve_closes_open_blocked_issue_with_comment(harness: Harness, workflow) -> None:
    title = workflow["env"]["BLOCKED_ISSUE_TITLE"]
    label = workflow["env"]["BLOCKED_ISSUE_LABEL"]
    harness.seed_issue(title, label=label, number=9)

    completed = harness.run("resolve", "Close the blocked-audit issue", event="schedule")

    assert completed.returncode == 0, completed.stderr
    issue = harness.snapshot()["issues"][0]
    assert issue["state"] == "closed"
    assert len(issue["comments"]) == 1 and "성공" in issue["comments"][0]


@posix_only
def test_resolve_is_a_no_op_without_a_blocked_issue(harness: Harness) -> None:
    completed = harness.run("resolve", "Close the blocked-audit issue", event="schedule")

    assert completed.returncode == 0, completed.stderr
    assert harness.gh_calls() == ["issue list"]


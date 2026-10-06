"""
Tests for the live-path audit fixes that don't need a CDK synth.

(Template-level checks for AppConfig, the secret ARN, and IAM live in
tests/test_deployment_consistency.py next to the shared synth fixture.)
"""

import ast
import importlib.util
import inspect
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===== 1/7. Feature flags: AppConfig Data session flow, local mode =====


class FakeAppConfigData:
    """Stands in for boto3.client("appconfigdata")."""

    def __init__(self, bodies, interval=15):
        self.bodies = list(bodies)
        self.interval = interval
        self.sessions = []
        self.tokens = []

    def start_configuration_session(self, **kwargs):
        self.sessions.append(kwargs)
        return {"InitialConfigurationToken": "token-0"}

    def get_latest_configuration(self, ConfigurationToken):  # noqa: N803
        self.tokens.append(ConfigurationToken)
        body = self.bodies.pop(0)
        return {
            "NextPollConfigurationToken": f"token-{len(self.tokens)}",
            "NextPollIntervalInSeconds": self.interval,
            "Configuration": io.BytesIO(body),
            "ContentType": "application/json",
        }


def test_flag_client_uses_the_session_and_next_token_flow(monkeypatch):
    from demo_app import feature_flags

    fake = FakeAppConfigData(
        [b'{"discount_v2": {"enabled": false}}', b"", b'{"discount_v2": {"enabled": true}}']
    )
    client = feature_flags.AppConfigFlagClient(
        application="MiniDebugAssist",
        environment="production",
        profile="feature-flags",
        region="us-east-1",
        client=fake,
    )
    clock = [1000.0]
    monkeypatch.setattr(feature_flags.time, "monotonic", lambda: clock[0])

    assert client.get_flag("DISCOUNT_V2") == "off"
    assert fake.sessions == [
        {
            "ApplicationIdentifier": "MiniDebugAssist",
            "EnvironmentIdentifier": "production",
            "ConfigurationProfileIdentifier": "feature-flags",
            "RequiredMinimumPollIntervalInSeconds": 15,
        }
    ]

    # Within the poll interval: served from memory, no API call
    clock[0] += 5
    assert client.get_flag("DISCOUNT_V2") == "off"
    assert fake.tokens == ["token-0"]

    # Next poll: empty body means "unchanged", keep the previous flags
    clock[0] += 15
    assert client.get_flag("DISCOUNT_V2") == "off"
    # Then the flag is flipped in AppConfig
    clock[0] += 15
    assert client.get_flag("DISCOUNT_V2") == "on"
    assert fake.tokens == ["token-0", "token-1", "token-2"]  # each poll uses the next token
    assert len(fake.sessions) == 1


def test_flag_client_starts_a_new_session_after_an_error(monkeypatch):
    from demo_app import feature_flags
    from demo_app.feature_flags import AppConfigFlagClient

    clock = [1000.0]
    monkeypatch.setattr(feature_flags.time, "monotonic", lambda: clock[0])

    class Expiring(FakeAppConfigData):
        def get_latest_configuration(self, ConfigurationToken):  # noqa: N803  # noqa: N803
            if ConfigurationToken == "token-0" and len(self.sessions) == 1:
                raise RuntimeError("BadRequestException: token expired")
            return super().get_latest_configuration(ConfigurationToken)

    fake = Expiring([b'{"discount_v2": {"enabled": true}}'])
    client = AppConfigFlagClient(client=fake, region="us-east-1")
    with pytest.raises(RuntimeError):
        client.get_flags()
    # Backs off: no new AppConfig call until the poll interval has passed
    assert client.get_flags() == {}
    assert len(fake.sessions) == 1
    clock[0] += 15
    assert client.get_flag("discount_v2") == "on"
    assert len(fake.sessions) == 2


def test_demo_config_reads_appconfig_when_enabled(monkeypatch):
    import demo_app.config as config_module

    monkeypatch.delenv("DISCOUNT_V2", raising=False)
    monkeypatch.setenv("USE_AWS_APPCONFIG", "true")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    created = {}

    def fake_client(service, **kwargs):
        created["service"], created["kwargs"] = service, kwargs
        return FakeAppConfigData([b'{"discount_v2": {"enabled": true}}'])

    import boto3

    monkeypatch.setattr(boto3, "client", fake_client)
    cfg = config_module.Config()
    assert cfg.appconfig_env == "production"
    assert cfg.get_flag("DISCOUNT_V2") == "on"
    assert created == {"service": "appconfigdata", "kwargs": {"region_name": "us-west-2"}}

    # The env var still overrides AppConfig
    monkeypatch.setenv("DISCOUNT_V2", "off")
    assert cfg.get_flag("DISCOUNT_V2") == "off"


def test_demo_config_local_mode_never_calls_aws(monkeypatch):
    import boto3

    import demo_app.config as config_module

    def no_aws(*args, **kwargs):
        raise AssertionError("local mode must not create AWS clients")

    monkeypatch.setattr(boto3, "client", no_aws)
    monkeypatch.delenv("USE_AWS_APPCONFIG", raising=False)
    monkeypatch.delenv("DISCOUNT_V2", raising=False)
    cfg = config_module.Config()
    assert cfg.get_flag("DISCOUNT_V2") == "off"
    monkeypatch.setenv("DISCOUNT_V2", "on")
    assert cfg.get_flag("DISCOUNT_V2") == "on"


def test_appconfig_unreachable_falls_back_to_default(monkeypatch):
    import boto3

    import demo_app.config as config_module

    def broken(*args, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(boto3, "client", broken)
    monkeypatch.setenv("USE_AWS_APPCONFIG", "true")
    monkeypatch.delenv("DISCOUNT_V2", raising=False)
    assert config_module.Config().get_flag("DISCOUNT_V2") == "off"


def test_build_flags_document_rejects_invalid_keys():
    from demo_app.feature_flags import build_flags_document

    with pytest.raises(ValueError):
        build_flags_document({"1bad": True})


# ===== 2. Validation runs only the demo app tests, in mock mode =====


def test_validate_runs_only_demo_app_tests_in_mock_mode(monkeypatch):
    from agent.config import AgentConfig
    from agent.nodes import validate
    from agent.state import AgentState

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="1 passed", stderr="")

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    monkeypatch.setenv("MCP_MOCK_MODE", "false")
    state = AgentState(issue_id="T-1", issue_title="t", issue_data={})
    validate.validate_node(state, AgentConfig(mode="aws"))

    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "pytest"]
    assert cmd[-1] == "tests/test_demo_app.py"
    assert kwargs["env"]["MCP_MOCK_MODE"] == "true"
    assert kwargs["env"]["PYTHONPATH"] == kwargs["cwd"]
    assert state.validation_result.passed


# ===== 3. Patches are applied with git apply (git is in the image) =====

ORIGINAL = "def f(user):\n    a = 1\n    email = user['email']\n    return email\n"
FIXED = "def f(user):\n    a = 1\n    email = user.get('email')\n    return email\n"


@pytest.mark.parametrize(
    "diff",
    [
        # a/ b/ prefixes with context
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,4 +1,4 @@\n def f(user):\n     a = 1\n"
        "-    email = user['email']\n+    email = user.get('email')\n     return email\n",
        # no prefixes, wrong hunk counts (LLM style)
        "--- pkg/mod.py\n+++ pkg/mod.py\n@@ -2,9 +2,9 @@\n     a = 1\n"
        "-    email = user['email']\n+    email = user.get('email')\n     return email",
        # zero context lines
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -3,1 +3,1 @@\n"
        "-    email = user['email']\n+    email = user.get('email')\n",
    ],
)
def test_patch_file_content_applies_llm_style_diffs(diff):
    from agent.patching import patch_file_content

    assert patch_file_content(ORIGINAL, diff, "pkg/mod.py") == FIXED


def test_bad_diff_fails_without_touching_the_tree(tmp_path):
    from agent.patching import PatchError, apply_unified_diff, patch_file_content

    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_text(ORIGINAL)
    bad = "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,1 +1,1 @@\n-nothing like this\n+x\n"
    ok, error = apply_unified_diff(bad, tmp_path)
    assert not ok and "git apply" in error
    assert target.read_text() == ORIGINAL
    with pytest.raises(PatchError):
        patch_file_content(ORIGINAL, bad, "pkg/mod.py")


def test_patch_can_create_a_new_file():
    from agent.patching import patch_file_content

    diff = "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
    assert patch_file_content(None, diff, "pkg/new.py") == "x = 1\ny = 2\n"


def test_agent_never_shells_out_to_patch_and_image_has_git():
    for path in (REPO_ROOT / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value == "patch":
                    raise AssertionError(f"{path} runs `patch`, which the image doesn't have")
    dockerfile = (REPO_ROOT / "agent" / "Dockerfile").read_text()
    apt_line = dockerfile.split("apt-get install", 1)[1].split("rm -rf", 1)[0]
    assert "git" in apt_line.split()


# ===== 5. MCP servers start lazily and really launch =====


@pytest.fixture
def real_mcp_env(monkeypatch):
    """Real (non-mock) MCP mode with no credentials, so nothing reaches AWS or GitHub."""
    monkeypatch.setenv("MCP_MOCK_MODE", "false")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("GITHUB_REPO", "octo/repo")
    monkeypatch.setenv("DEMO_APP_LOG_GROUP", "/aws/ecs/mini-debug-assist-demo")


def test_mcp_servers_start_lazily_on_first_tool_call(real_mcp_env):
    from agent.mcp_client import MCPClient

    client = MCPClient()
    try:
        assert client.servers == {}
        # The agent's tool spec passes just {"path": ...}; repo comes from GITHUB_REPO
        result = client.call_tool("read_file", {"path": "demo_app/main.py"})
        assert set(client.servers) == {"github_mcp"}
        assert "GITHUB_TOKEN environment variable not set" in json.dumps(result)

        result = client.call_tool("search_code", {"pattern": "KeyError"})
        assert set(client.servers) == {"github_mcp"}  # reused, not restarted
        assert "GITHUB_TOKEN" in json.dumps(result)

        # {"query": ...} only: the log group defaults to DEMO_APP_LOG_GROUP
        result = client.call_tool("query_logs", {"query": "KeyError", "time_range_minutes": 30})
        assert "cloudwatch_logs" in client.servers
        assert "Error querying logs" in json.dumps(result)

        result = client.call_tool("get_flag", {"flag_name": "DISCOUNT_V2"})
        assert "appconfig_flags" in client.servers
        assert "Error getting flag" in json.dumps(result)

        names = {t["name"] for tools in client.tools_cache.values() for t in tools}
        assert {"search_code", "read_file", "query_logs", "get_flag"} <= names
    finally:
        client.stop_all()


@pytest.mark.parametrize("module", ["cloudwatch_logs", "xray_mcp", "github_mcp", "appconfig_flags"])
def test_each_mcp_server_answers_initialize_and_tools_list(real_mcp_env, module):
    """`python -m mcp_servers.<name>` must import and serve (the old main() never did)."""
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    # Talk to it like the agent does: one request at a time, waiting for each
    # answer (closing stdin early would end the session before tools/list).
    import select

    proc = subprocess.Popen(
        [sys.executable, "-m", f"mcp_servers.{module}"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    responses = {}
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            if "id" not in request:
                continue
            while request["id"] not in responses:
                ready, _, _ = select.select([proc.stdout], [], [], 60)
                line = proc.stdout.readline() if ready else ""
                assert line, f"no answer to {request['method']} (exit code {proc.poll()})"
                msg = json.loads(line)
                if "id" in msg:
                    responses[msg["id"]] = msg
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
        proc.stdout.close()
        proc.stderr.close()

    assert responses[1]["result"]["serverInfo"]["name"]
    assert responses[2]["result"]["tools"]


def test_mcp_client_reports_a_server_that_dies(real_mcp_env, monkeypatch):
    """A server that exits (e.g. import error) fails fast instead of spinning until timeout."""
    import time

    from agent import mcp_client

    monkeypatch.setitem(
        mcp_client.MCP_SERVERS,
        "github_mcp",
        {"command": sys.executable, "args": ["-c", "import sys; sys.exit(3)"]},
    )
    client = mcp_client.MCPClient()
    started = time.time()
    result = client.call_tool("read_file", {"path": "x"})
    assert result["success"] is False
    assert "Failed to start MCP server 'github_mcp'" in result["error"]
    assert time.time() - started < 10


def test_mcp_tool_arguments_match_the_agent_tool_specs():
    """The LLM tool specs' argument names are accepted by the servers."""
    cloudwatch = _load(REPO_ROOT / "mcp_servers" / "cloudwatch_logs.py", "cw_mcp")

    assert cloudwatch.insights_query("KeyError") == (
        'fields @timestamp, @message | filter @message like "KeyError" '
        "| sort @timestamp desc | limit 50"
    )
    query = 'fields @message | filter level = "ERROR"'
    assert cloudwatch.insights_query(query) == query
    assert '\\"' in cloudwatch.insights_query('say "hi"')


# ===== 8. trigger_bug default =====


def test_trigger_bug_sends_20_errors_by_default():
    trigger = _load(REPO_ROOT / "scripts" / "trigger_bug.py", "trigger_bug")
    default = inspect.signature(trigger.trigger_errors).parameters["count"].default
    assert default == 20
    assert default >= 2 * trigger.ALARM_THRESHOLD


# ===== 9. Unique fix branch per investigation =====


def test_fix_branch_name_is_unique_per_alarm_event():
    from agent.nodes.create_diff import fix_branch_name
    from agent.state import AgentState

    def state(event_id):
        return AgentState(
            issue_id="ALARM-mini-debug-assist-error-alarm-2026-10-06",
            issue_title="t",
            issue_data={"alarm_event_id": event_id} if event_id is not None else {},
        )

    first = fix_branch_name(state("c4c1c1c9-6542-e61b-6ef0-8c4d36933a92"))
    second = fix_branch_name(state("0d3f2a11-0000-4000-8000-000000000000"))
    assert first == "fix/debug-assist-alarm-mini-debug-assist-error-alarm-2026-10-06-c4c1c1c9"
    assert first != second
    fallback = fix_branch_name(state(None))
    assert fallback.startswith("fix/debug-assist-alarm-mini-debug-assist-error-alarm-2026-10-06-")
    assert fallback.rsplit("-", 1)[1].isdigit()
    for name in (first, second, fallback):
        check = subprocess.run(["git", "check-ref-format", f"refs/heads/{name}"])
        assert check.returncode == 0, name


# ===== 10. X-Ray is opt-in (the demo app isn't instrumented) =====


def test_context_collector_skips_xray_unless_enabled(monkeypatch):
    from agent.config import AgentConfig
    from agent.nodes import context_collector
    from agent.state import AgentState

    monkeypatch.setattr(context_collector, "_fetch_cloudwatch_logs", lambda d, c: [])
    monkeypatch.setattr(context_collector, "_fetch_code_context", lambda d, c: {})
    called = []
    monkeypatch.setattr(
        context_collector,
        "_fetch_xray_traces",
        lambda d, c: called.append(1) or [{"id": "t"}],
    )

    monkeypatch.delenv("XRAY_TRACES_ENABLED", raising=False)
    state = AgentState(issue_id="T", issue_title="t", issue_data={})
    state = context_collector.context_collector_node(state, AgentConfig(mode="aws"))
    assert state.traces == [] and called == []

    monkeypatch.setenv("XRAY_TRACES_ENABLED", "true")
    state = context_collector.context_collector_node(state, AgentConfig(mode="aws"))
    assert state.traces == [{"id": "t"}] and called == [1]


def test_demo_app_does_not_pretend_to_trace():
    source = (REPO_ROOT / "demo_app" / "main.py").read_text()
    assert "aws_xray_sdk" not in source
    assert "USE_XRAY" not in source


# ===== 11. Region =====


def _boto3_calls_without_region():
    missing = []
    for folder in ("agent", "mcp_servers", "demo_app", "scripts"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"client", "resource"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "boto3"
                    and not any(k.arg == "region_name" for k in node.keywords)
                ):
                    missing.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return missing


def test_every_boto3_client_gets_an_explicit_region():
    assert _boto3_calls_without_region() == []


def test_agent_image_does_not_hardcode_a_region():
    assert "AWS_DEFAULT_REGION" not in (REPO_ROOT / "agent" / "Dockerfile").read_text()


def test_dedup_uses_the_task_region(monkeypatch):
    import boto3

    from agent import dedup

    seen = {}

    class Table:
        def put_item(self, **kwargs):
            seen["item"] = kwargs["Item"]

    class Resource:
        def Table(self, name):  # noqa: N802
            seen["table"] = name
            return Table()

    def fake_resource(service, **kwargs):
        seen["service"], seen["kwargs"] = service, kwargs
        return Resource()

    monkeypatch.setattr(boto3, "resource", fake_resource)
    monkeypatch.setenv("DEDUP_TABLE_NAME", "mini-debug-assist-dedup")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("DEDUP_WINDOW_SECONDS", raising=False)
    assert dedup.should_investigate("sig") is True
    assert seen["service"] == "dynamodb"
    assert seen["kwargs"] == {"region_name": "us-west-2"}
    # 12. Shorter default window: 15 minutes
    assert seen["item"]["ttl"] - seen["item"]["first_seen"] == 900

    monkeypatch.setenv("DEDUP_WINDOW_SECONDS", "60")
    dedup.should_investigate("sig")
    assert seen["item"]["ttl"] - seen["item"]["first_seen"] == 60


@pytest.mark.parametrize(
    "region, ok",
    [("us-east-1", True), ("us-west-2", True), ("eu-west-1", False), (None, False)],
)
def test_doctor_warns_outside_us_regions(region, ok):
    doctor = _load(REPO_ROOT / "scripts" / "doctor.py", "doctor")
    passed, message = doctor.check_region(region)
    assert passed is ok
    if region and not ok:
        assert "us-east-1" in message and "US region" in message


def test_readme_documents_us_regions_and_dedup_reset():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "us-east-1" in readme and "us." in readme
    assert "make reset-dedup" in readme
    assert "XRAY_TRACES_ENABLED" in readme


# ===== 12. make reset-dedup =====


def test_reset_dedup_deletes_every_record():
    reset = _load(REPO_ROOT / "scripts" / "reset_dedup.py", "reset_dedup")
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[2] == "scan":
            items = {"Items": [{"error_signature": {"S": "a"}}, {"error_signature": {"S": "b"}}]}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(items), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    assert reset.reset_dedup(run) == 2
    deletes = [c for c in calls if c[2] == "delete-item"]
    assert [json.loads(c[-1]) for c in deletes] == [
        {"error_signature": {"S": "a"}},
        {"error_signature": {"S": "b"}},
    ]
    assert all("mini-debug-assist-dedup" in c for c in calls)

    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "\nreset-dedup:" in makefile and "scripts/reset_dedup.py" in makefile


# ===== 13. setup-secrets uses GITHUB_REPO; pinned dependencies =====


def test_setup_secrets_defaults_to_the_deploy_repo_and_warns_on_mismatch(tmp_path):
    script = REPO_ROOT / "scripts" / "setup_github_token.sh"
    text = script.read_text()
    assert "resolve_github_repo" in text

    # Run it with a fake `aws` CLI on PATH; enter a token and a different repo
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "aws.log"
    fake_aws = bin_dir / "aws"
    fake_aws.write_text(f'#!/usr/bin/env bash\necho "$@" >> {log}\nexit 0\n')
    fake_aws.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GITHUB_REPO": "octo/deploy"}

    result = subprocess.run(
        ["bash", str(script)], input="tok\nocto/other\n", text=True, capture_output=True, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "octo/deploy" in result.stdout
    assert "⚠️  You entered octo/other" in result.stdout

    # Enter accepts the deploy repo, no warning
    result = subprocess.run(
        ["bash", str(script)], input="tok\n\n", text=True, capture_output=True, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "You entered" not in result.stdout
    assert '"repo":"octo/deploy"' in log.read_text()


def test_main_dependencies_are_pinned():
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = pyproject["project"]["dependencies"]
    assert deps and all("==" in d for d in deps), [d for d in deps if "==" not in d]
    mcp = next(d for d in deps if d.startswith("mcp=="))
    assert mcp.startswith("mcp==1."), "the servers use the mcp 1.x server API"
    demo_reqs = [
        line
        for line in (REPO_ROOT / "demo_app" / "requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert demo_reqs and all("==" in line for line in demo_reqs)

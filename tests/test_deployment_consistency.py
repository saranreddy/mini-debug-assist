"""
Test deployment consistency between CDK stacks and scripts.

Prevents drift between infrastructure definitions and operational scripts.
"""

import json
import os
import subprocess

import pytest

# infra/app.py refuses to synth without a valid GITHUB_REPO (see infra/deploy_config.py).
# Use the caller's value if set, else a well-formed example, for every synth below.
TEST_GITHUB_REPO = "example-owner/mini-debug-assist"
CDK_ENV = {**os.environ, "GITHUB_REPO": os.environ.get("GITHUB_REPO") or TEST_GITHUB_REPO}


def test_cdk_synth_produces_expected_outputs():
    """Test that CDK synth produces expected stack names and outputs."""
    # Install CDK dependencies if not already installed
    subprocess.run(
        ["pip", "install", "-q", "-r", "requirements.txt"],
        cwd="infra",
        capture_output=True,
    )

    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "--quiet"],
        cwd="infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )

    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"

    # Read the assembly manifest to verify stack names
    with open("infra/cdk.out/manifest.json") as f:
        manifest = json.load(f)

    artifacts = manifest["artifacts"]

    # Check stack names match what scripts expect
    expected_stacks = [
        "MiniDebugAssist-DemoApp",
        "MiniDebugAssist-Agent",
        "MiniDebugAssist-Observability",
    ]

    actual_stacks = [
        name
        for name, artifact in artifacts.items()
        if artifact.get("type") == "aws:cloudformation:stack"
    ]

    for expected_stack in expected_stacks:
        assert (
            expected_stack in actual_stacks
        ), f"Stack {expected_stack} not found in CDK output. Found: {actual_stacks}"


def test_demo_app_stack_has_url_output():
    """Test that DemoApp stack exports DemoAppUrl output."""
    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "MiniDebugAssist-DemoApp", "--quiet"],
        cwd="infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )

    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"

    # Read the template
    with open("infra/cdk.out/MiniDebugAssist-DemoApp.template.json") as f:
        template = json.load(f)

    # Check for DemoAppUrl output
    outputs = template.get("Outputs", {})
    assert "DemoAppUrl" in outputs, f"DemoAppUrl output not found. Outputs: {list(outputs.keys())}"


def test_alarm_name_matches_scripts():
    """Test that alarm name in CDK matches what scripts expect."""
    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "MiniDebugAssist-Observability", "--quiet"],
        cwd="infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )

    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"

    # Read the template
    with open("infra/cdk.out/MiniDebugAssist-Observability.template.json") as f:
        template = json.load(f)

    # Find the alarm
    resources = template.get("Resources", {})
    alarms = {
        name: res for name, res in resources.items() if res.get("Type") == "AWS::CloudWatch::Alarm"
    }

    assert len(alarms) > 0, "No CloudWatch alarms found in template"

    # Check alarm name and threshold
    alarm_resource = list(alarms.values())[0]
    props = alarm_resource.get("Properties", {})

    expected_alarm_name = "mini-debug-assist-error-alarm"
    expected_threshold = 10

    actual_alarm_name = props.get("AlarmName")
    actual_threshold = props.get("Threshold")

    assert (
        actual_alarm_name == expected_alarm_name
    ), f"Alarm name mismatch: expected {expected_alarm_name}, got {actual_alarm_name}"
    assert (
        actual_threshold == expected_threshold
    ), f"Alarm threshold mismatch: expected {expected_threshold}, got {actual_threshold}"


def test_cluster_name_matches_scripts():
    """Test that ECS cluster name in CDK matches what scripts expect."""
    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "MiniDebugAssist-DemoApp", "--quiet"],
        cwd="infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )

    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"

    # Read the template
    with open("infra/cdk.out/MiniDebugAssist-DemoApp.template.json") as f:
        template = json.load(f)

    # Find the cluster
    resources = template.get("Resources", {})
    clusters = {
        name: res for name, res in resources.items() if res.get("Type") == "AWS::ECS::Cluster"
    }

    assert len(clusters) > 0, "No ECS clusters found in template"

    cluster_resource = list(clusters.values())[0]
    props = cluster_resource.get("Properties", {})

    expected_cluster_name = "mini-debug-assist-cluster"
    actual_cluster_name = props.get("ClusterName")

    assert (
        actual_cluster_name == expected_cluster_name
    ), f"Cluster name mismatch: expected {expected_cluster_name}, got {actual_cluster_name}"


def test_task_family_matches_scripts():
    """Test that ECS task definition family in CDK matches what scripts expect."""
    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "MiniDebugAssist-Agent", "--quiet"],
        cwd="infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )

    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"

    # Read the template
    with open("infra/cdk.out/MiniDebugAssist-Agent.template.json") as f:
        template = json.load(f)

    # Find the task definition
    resources = template.get("Resources", {})
    task_defs = {
        name: res
        for name, res in resources.items()
        if res.get("Type") == "AWS::ECS::TaskDefinition"
    }

    assert len(task_defs) > 0, "No ECS task definitions found in template"

    task_def_resource = list(task_defs.values())[0]
    props = task_def_resource.get("Properties", {})

    expected_family = "mini-debug-assist-agent"
    actual_family = props.get("Family")

    assert (
        actual_family == expected_family
    ), f"Task family mismatch: expected {expected_family}, got {actual_family}"


def test_secret_is_imported_not_created():
    """Test that agent stack imports the GitHub token secret instead of creating it."""
    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "MiniDebugAssist-Agent", "--quiet"],
        cwd="infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )

    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"

    # Read the template
    with open("infra/cdk.out/MiniDebugAssist-Agent.template.json") as f:
        template = json.load(f)

    # Check that no AWS::SecretsManager::Secret resource is created
    resources = template.get("Resources", {})
    secrets = {
        name: res
        for name, res in resources.items()
        if res.get("Type") == "AWS::SecretsManager::Secret"
    }

    # The stack should NOT create a secret (it imports existing one)
    assert (
        len(secrets) == 0
    ), f"Agent stack should import secret, not create it. Found: {list(secrets.keys())}"


# ---------------------------------------------------------------------------
# Guards for the first-live-deploy fixes (alarm/metric filter, demo app image,
# EventBridge rule name, README names). These synth once into a temp directory
# and read the templates, so they need neither AWS credentials nor Docker.
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
STACKS = ["MiniDebugAssist-DemoApp", "MiniDebugAssist-Agent", "MiniDebugAssist-Observability"]


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """Synth all stacks once; return (templates by stack name, cdk.out dir)."""
    out_dir = tmp_path_factory.mktemp("cdk-out")
    subprocess.run(
        ["pip", "install", "-q", "-r", "requirements.txt"],
        cwd=REPO_ROOT / "infra",
        capture_output=True,
    )
    result = subprocess.run(
        ["npx", "aws-cdk@latest", "synth", "--quiet", "-o", str(out_dir)],
        cwd=REPO_ROOT / "infra",
        capture_output=True,
        text=True,
        env=CDK_ENV,
    )
    assert result.returncode == 0, f"CDK synth failed: {result.stderr}"
    templates = {
        name: json.loads((out_dir / f"{name}.template.json").read_text()) for name in STACKS
    }
    return templates, out_dir


def _resources(template, resource_type):
    return [
        res for res in template.get("Resources", {}).values() if res.get("Type") == resource_type
    ]


def _single(template, resource_type):
    found = _resources(template, resource_type)
    assert len(found) == 1, f"Expected exactly one {resource_type}, found {len(found)}"
    return found[0]["Properties"]


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alarm_watches_exactly_the_metric_the_filter_publishes(synth):
    """The alarm must use the filter's namespace, metric name, and (no) dimensions."""
    templates, _ = synth
    metric_filter = _single(templates["MiniDebugAssist-DemoApp"], "AWS::Logs::MetricFilter")
    transform = metric_filter["MetricTransformations"][0]
    alarm = _single(templates["MiniDebugAssist-Observability"], "AWS::CloudWatch::Alarm")

    assert '$.level = "ERROR"' in metric_filter["FilterPattern"]
    assert transform["MetricNamespace"] == "MiniDebugAssist/Demo"
    assert alarm["Namespace"] == transform["MetricNamespace"]
    assert alarm["MetricName"] == transform["MetricName"]
    # An alarm only matches datapoints with exactly its dimensions.
    assert not transform.get("Dimensions"), "Metric filter should publish ErrorCount undimensioned"
    assert not alarm.get("Dimensions"), "Alarm should watch the undimensioned ErrorCount metric"

    assert alarm["AlarmName"] == "mini-debug-assist-error-alarm"
    assert alarm["Statistic"] == "Sum"
    assert alarm["Period"] == 300
    assert alarm["EvaluationPeriods"] == 1
    assert alarm["Threshold"] == 10
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"


def test_demo_app_error_logs_match_the_metric_filter():
    """demo_app must log a JSON "level": "ERROR" field, which the filter matches on."""
    import logging

    from demo_app.main import formatter

    record = logging.LogRecord("demo_app", logging.ERROR, __file__, 1, "boom", None, None)
    record.exception_type = "KeyError"
    line = json.loads(formatter.format(record))
    assert line["level"] == "ERROR"
    assert line["exception_type"] == "KeyError"


def test_smoke_rule_name_matches_cdk_rule(synth):
    """scripts/smoke.py must look up the rule by the exact name CDK gives it."""
    templates, _ = synth
    rule = _single(templates["MiniDebugAssist-Agent"], "AWS::Events::Rule")
    smoke = _load_script("smoke")

    assert rule.get("Name") == "mini-debug-assist-agent-trigger"
    assert smoke.AGENT_TRIGGER_RULE_NAME == rule["Name"]
    source = (REPO_ROOT / "scripts" / "smoke.py").read_text()
    assert '"describe-rule", "--name", AGENT_TRIGGER_RULE_NAME' in source


def test_readme_names_match_cdk(synth):
    """Every CloudWatch namespace and alarm name the README tells you to use must exist."""
    templates, _ = synth
    transform = _single(templates["MiniDebugAssist-DemoApp"], "AWS::Logs::MetricFilter")[
        "MetricTransformations"
    ][0]
    alarm = _single(templates["MiniDebugAssist-Observability"], "AWS::CloudWatch::Alarm")
    readme = (REPO_ROOT / "README.md").read_text()

    namespaces = set(re.findall(r'--namespace\s+"?([\w/.-]+)"?', readme))
    assert namespaces, "README should show the CloudWatch metric namespace"
    assert namespaces == {transform["MetricNamespace"]}, f"README namespaces: {namespaces}"
    assert f'--metric-name {transform["MetricName"]}' in readme

    alarm_names = set(re.findall(r"--alarm-names\s+([\w-]+)", readme))
    assert alarm_names == {alarm["AlarmName"]}, f"README alarm names: {alarm_names}"


def test_demo_app_uses_asset_image_not_stock_python(synth):
    """The demo app task must run the image built from demo_app/Dockerfile."""
    templates, out_dir = synth
    task_defs = _resources(templates["MiniDebugAssist-DemoApp"], "AWS::ECS::TaskDefinition")
    containers = [
        c
        for td in task_defs
        for c in td["Properties"]["ContainerDefinitions"]
        if c["Name"] == "DemoApp"
    ]
    assert len(containers) == 1
    container = containers[0]

    image = json.dumps(container["Image"])
    assert "python:3.11-slim" not in image, "Demo app still uses the stock python image"
    assert "container-assets" in image, f"Expected a CDK asset image, got {image}"

    # The asset manifest points at a staged copy of demo_app/ with the Dockerfile
    assets = json.loads((out_dir / "MiniDebugAssist-DemoApp.assets.json").read_text())
    docker_images = assets.get("dockerImages", {})
    assert len(docker_images) == 1
    asset = next(iter(docker_images.values()))
    assert asset["source"].get("platform") == "linux/amd64"
    staged = out_dir / asset["source"]["directory"]
    assert (staged / "Dockerfile").is_file()
    assert (staged / "main.py").is_file()
    assert (staged / "requirements.txt").is_file()

    # Port, health check, and logs line up with the app
    dockerfile = (REPO_ROOT / "demo_app" / "Dockerfile").read_text()
    assert "demo_app.main:app" in dockerfile and '"--port", "8000"' in dockerfile
    assert [p["ContainerPort"] for p in container["PortMappings"]] == [8000]
    target_group = _single(
        templates["MiniDebugAssist-DemoApp"], "AWS::ElasticLoadBalancingV2::TargetGroup"
    )
    assert target_group["HealthCheckPath"] == "/health"
    assert container["LogConfiguration"]["LogDriver"] == "awslogs"
    log_group = _single(templates["MiniDebugAssist-DemoApp"], "AWS::Logs::LogGroup")
    assert log_group["LogGroupName"] == "/aws/ecs/mini-debug-assist-demo"


# ---------------------------------------------------------------------------
# Guards for the agent image build, agent task networking, and GITHUB_REPO.
# ---------------------------------------------------------------------------


def _dockerfile_copy_sources(dockerfile: Path) -> list[str]:
    """Source paths of every COPY/ADD instruction (ignores comments and --flags)."""
    sources = []
    for raw in dockerfile.read_text().splitlines():
        parts = raw.split()
        if not parts or parts[0].upper() not in {"COPY", "ADD"}:
            continue
        args = [p for p in parts[1:] if not p.startswith("--")]
        sources.extend(args[:-1])  # last argument is the destination
    return sources


def test_agent_image_builds_from_repo_root_and_every_copy_resolves(synth):
    """The agent asset's context is the repo root, and the staged context has every COPY source."""
    _, out_dir = synth
    assets = json.loads((out_dir / "MiniDebugAssist-Agent.assets.json").read_text())
    docker_images = assets.get("dockerImages", {})
    assert len(docker_images) == 1
    source = next(iter(docker_images.values()))["source"]
    assert source.get("dockerFile") == "agent/Dockerfile"
    assert source.get("platform") == "linux/amd64"

    staged = out_dir / source["directory"]
    sources = _dockerfile_copy_sources(REPO_ROOT / "agent" / "Dockerfile")
    assert {"pyproject.toml", "agent", "demo_app", "mcp_servers", "skills", "tests"} <= set(sources)
    for src in sources:
        assert (REPO_ROOT / src).exists(), f"agent/Dockerfile copies {src}, missing in the repo"
        assert (staged / src).exists(), f"agent/Dockerfile copies {src}, missing in build context"
    assert (staged / "agent" / "Dockerfile").is_file()

    # Excluded: secrets, big files, and things the image does not need
    for excluded in [".git", ".env", "docs", "infra", "output"]:
        assert not (staged / excluded).exists(), f"{excluded} should not be in the build context"
    assert not list(staged.rglob("*.mp4")), "Videos should not be in the build context"


def test_agent_task_gets_public_ip_and_outbound_access(synth):
    """Public subnets + no NAT: the EventBridge-launched task needs a public IP and egress."""
    templates, _ = synth
    agent = templates["MiniDebugAssist-Agent"]
    rule = _single(agent, "AWS::Events::Rule")
    vpc_config = rule["Targets"][0]["EcsParameters"]["NetworkConfiguration"]["AwsVpcConfiguration"]
    assert vpc_config["AssignPublicIp"] == "ENABLED"
    assert all("PublicSubnet" in json.dumps(subnet) for subnet in vpc_config["Subnets"])

    groups = _resources(agent, "AWS::EC2::SecurityGroup")
    assert groups, "Expected a security group for the agent task"
    for group in groups:
        egress = group["Properties"].get("SecurityGroupEgress", [])
        assert any(
            e.get("CidrIp") == "0.0.0.0/0" and e.get("IpProtocol") == "-1" for e in egress
        ), "Agent task security group must allow all outbound traffic"


def test_agent_task_gets_the_configured_github_repo(synth):
    """GITHUB_REPO from the environment/.env lands in the agent container."""
    templates, _ = synth
    task_def = _single(templates["MiniDebugAssist-Agent"], "AWS::ECS::TaskDefinition")
    env = {
        e["Name"]: e["Value"]
        for c in task_def["ContainerDefinitions"]
        for e in c.get("Environment", [])
    }
    assert env.get("GITHUB_REPO") == CDK_ENV["GITHUB_REPO"]


def _load_deploy_config():
    spec = importlib.util.spec_from_file_location(
        "deploy_config", REPO_ROOT / "infra" / "deploy_config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_github_repo_resolution_rules(tmp_path):
    """Env wins, then .env; empty, placeholder, and malformed values are rejected."""
    deploy_config = _load_deploy_config()
    dotenv = tmp_path / ".env"
    missing = tmp_path / "missing.env"

    assert deploy_config.resolve_github_repo({"GITHUB_REPO": "octo/repo"}, missing) == "octo/repo"

    dotenv.write_text('# comment\nexport GITHUB_REPO="octo/from-dotenv"  \n')
    assert deploy_config.resolve_github_repo({}, dotenv) == "octo/from-dotenv"
    assert deploy_config.resolve_github_repo({"GITHUB_REPO": ""}, dotenv) == "octo/from-dotenv"
    assert deploy_config.resolve_github_repo({"GITHUB_REPO": "a/b"}, dotenv) == "a/b"

    bad_values = ["", "your-username/mini-debug-assist", "not-a-repo", "a/b/c", "https://x/y"]
    for bad in bad_values:
        dotenv.write_text(f"GITHUB_REPO={bad}\n")
        with pytest.raises(deploy_config.DeployConfigError, match="GITHUB_REPO"):
            deploy_config.resolve_github_repo({}, dotenv)
    with pytest.raises(deploy_config.DeployConfigError, match="not set"):
        deploy_config.resolve_github_repo({}, missing)


@pytest.mark.parametrize(
    "value, expected",
    [("", "GITHUB_REPO is not set"), ("your-username/mini-debug-assist", "placeholder")],
)
def test_cdk_app_refuses_empty_or_placeholder_github_repo(tmp_path, value, expected):
    """infra/app.py (run by cdk synth/deploy) exits with a clear message, deploying nothing."""
    import shutil

    # Copy infra/ into an empty fake repo so no real .env can supply a value
    infra = tmp_path / "repo" / "infra"
    shutil.copytree(
        REPO_ROOT / "infra", infra, ignore=shutil.ignore_patterns("cdk.out", "__pycache__")
    )
    result = subprocess.run(
        [sys.executable, "app.py"],
        cwd=infra,
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_REPO": value, "CDK_OUTDIR": str(tmp_path / "out")},
    )
    assert result.returncode == 1
    assert expected in result.stderr
    assert "export GITHUB_REPO=" in result.stderr
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_make_deploy_checks_github_repo_before_cdk_deploy():
    """`make deploy` runs the GITHUB_REPO check before calling cdk deploy."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    recipe = makefile.split("\ndeploy:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("infra/deploy_config.py") < recipe.index("cdk deploy")


def test_agent_github_token_injects_only_the_token_field(synth):
    """setup_github_token.sh stores {"token": ..., "repo": ...}; the agent needs just the token."""
    templates, _ = synth
    agent = templates["MiniDebugAssist-Agent"]
    task_defs = [
        r
        for r in _resources(agent, "AWS::ECS::TaskDefinition")
        if any(c.get("Secrets") for c in r["Properties"]["ContainerDefinitions"])
    ]
    assert task_defs, "agent task definition with secrets not found"
    secrets = {
        s["Name"]: s["ValueFrom"]
        for c in task_defs[0]["Properties"]["ContainerDefinitions"]
        for s in c.get("Secrets", [])
    }
    assert "GITHUB_TOKEN" in secrets
    value_from = json.dumps(secrets["GITHUB_TOKEN"])
    assert "mini-debug-assist/github-token" in value_from
    assert ":token::" in value_from, f"GITHUB_TOKEN must select the token field: {value_from}"


def test_agent_bedrock_policy_matches_inference_profile_arns(synth):
    """Inference-profile ARNs carry the account ID; models are allowed in every region."""
    templates, _ = synth
    agent = templates["MiniDebugAssist-Agent"]
    statements = [
        st
        for p in _resources(agent, "AWS::IAM::Policy")
        for st in p["Properties"]["PolicyDocument"]["Statement"]
        if st.get("Sid") == "BedrockInvokeModel"
    ]
    assert len(statements) == 1
    resources = statements[0]["Resource"]
    profiles = [json.dumps(r) for r in resources if "inference-profile" in json.dumps(r)]
    assert profiles, "no inference-profile resources"
    for r in profiles:
        assert "AWS::AccountId" in r, f"inference-profile ARN is missing the account: {r}"
    for prefix in ("us.anthropic.claude-", "global.anthropic.claude-"):
        assert any(prefix in r for r in profiles), f"no {prefix}* inference profile allowed"
    assert "arn:aws:bedrock:*::foundation-model/anthropic.claude-*" in resources
    assert "arn:aws:bedrock:::foundation-model/anthropic.claude-*" in resources


# ===== Alarm -> EventBridge -> agent task =====

ERROR_ALARM = "mini-debug-assist-error-alarm"

# A CloudWatch "Alarm State Change" event in the shape EventBridge delivers.
SAMPLE_ALARM_EVENT = {
    "version": "0",
    "id": "c4c1c1c9-6542-e61b-6ef0-8c4d36933a92",
    "detail-type": "CloudWatch Alarm State Change",
    "source": "aws.cloudwatch",
    "account": "123456789012",
    "time": "2026-10-06T16:05:00Z",
    "region": "us-east-1",
    "resources": [f"arn:aws:cloudwatch:us-east-1:123456789012:alarm:{ERROR_ALARM}"],
    "detail": {
        "alarmName": ERROR_ALARM,
        "state": {
            "value": "ALARM",
            "reason": (
                "Threshold Crossed: 1 out of the last 1 datapoints [12.0 (06/10/26 16:00:00)] "
                "was greater than or equal to the threshold (10.0) "
                "(minimum 1 datapoint for OK -> ALARM transition)."
            ),
            "timestamp": "2026-10-06T16:05:00.123+0000",
        },
        "previousState": {"value": "OK", "reason": "Threshold Crossed", "timestamp": "x"},
        "configuration": {
            "metrics": [
                {
                    "id": "m1",
                    "metricStat": {
                        "metric": {
                            "namespace": "MiniDebugAssist/Demo",
                            "name": "ErrorCount",
                            "dimensions": {},
                        },
                        "period": 300,
                        "stat": "Sum",
                    },
                    "returnData": True,
                }
            ]
        },
    },
}


def _with_alarm(name, state):
    event = json.loads(json.dumps(SAMPLE_ALARM_EVENT))
    event["detail"]["alarmName"] = name
    event["detail"]["state"]["value"] = state
    return event


def _pattern_matches(pattern, event):
    """Exact-value EventBridge matching (lists of allowed values, nested objects)."""
    for key, allowed in pattern.items():
        if key not in event:
            return False
        if isinstance(allowed, dict):
            if not isinstance(event[key], dict) or not _pattern_matches(allowed, event[key]):
                return False
        else:
            assert all(not isinstance(v, dict) for v in allowed), f"unsupported matcher: {allowed}"
            if event[key] not in allowed:
                return False
    return True


def _trigger_rule(templates):
    rules = [
        r
        for r in _resources(templates["MiniDebugAssist-Agent"], "AWS::Events::Rule")
        if r["Properties"].get("Name") == "mini-debug-assist-agent-trigger"
    ]
    assert len(rules) == 1
    return rules[0]["Properties"]


def _json_path(event, path):
    """Resolve a simple $.a.b.c path the way EventBridge input paths do."""
    assert path.startswith("$")
    value = event
    for part in [p for p in path[1:].split(".") if p]:
        value = value[part]
    return value


def _render_input_template(transformer, event):
    """
    Render an InputTransformer like EventBridge: a string variable placed unquoted
    gets quotes added (values are not escaped); objects/arrays are inserted as-is.
    """
    template = transformer["InputTemplate"]
    for var, path in transformer["InputPathsMap"].items():
        value = _json_path(event, path)
        rendered = f'"{value}"' if isinstance(value, str) else json.dumps(value)
        template = template.replace(f"<{var}>", rendered)
    return template


def test_trigger_rule_matches_only_the_error_alarm_entering_alarm(synth):
    templates, _ = synth
    pattern = _trigger_rule(templates)["EventPattern"]
    alarm = _single(templates["MiniDebugAssist-Observability"], "AWS::CloudWatch::Alarm")
    assert alarm["AlarmName"] == ERROR_ALARM
    assert pattern["source"] == ["aws.cloudwatch"]
    assert pattern["detail-type"] == ["CloudWatch Alarm State Change"]
    assert pattern["detail"] == {"alarmName": [ERROR_ALARM], "state": {"value": ["ALARM"]}}

    assert _pattern_matches(pattern, SAMPLE_ALARM_EVENT)
    assert not _pattern_matches(pattern, _with_alarm("some-other-alarm", "ALARM"))
    assert not _pattern_matches(pattern, _with_alarm(ERROR_ALARM, "OK"))
    assert not _pattern_matches(pattern, _with_alarm(ERROR_ALARM, "INSUFFICIENT_DATA"))


def test_alarm_name_is_shared_between_alarm_and_rule():
    """One constant feeds both the alarm and the rule, so they cannot drift apart."""
    stacks = REPO_ROOT / "infra" / "stacks"
    assert f'ERROR_ALARM_NAME = "{ERROR_ALARM}"' in (stacks / "demo_app_stack.py").read_text()
    for name in ("agent_stack.py", "observability_stack.py"):
        source = (stacks / name).read_text()
        assert "ERROR_ALARM_NAME" in source
        assert f'"{ERROR_ALARM}"' not in source, f"{name} hardcodes the alarm name"


def _rendered_overrides(templates):
    targets = _trigger_rule(templates)["Targets"]
    assert len(targets) == 1
    transformer = targets[0]["InputTransformer"]
    rendered = _render_input_template(transformer, SAMPLE_ALARM_EVENT)
    return transformer, json.loads(rendered)


def test_trigger_input_renders_valid_overrides_with_string_env_values(synth):
    templates, _ = synth
    transformer, overrides = _rendered_overrides(templates)
    template = transformer["InputTemplate"]

    # Every placeholder has an input path, and none is wrapped in quotes (EventBridge
    # adds the quotes for string values; pre-quoted values would not be escaped).
    placeholders = set(re.findall(r"<([A-Za-z0-9_.-]+)>", template))
    assert placeholders == set(transformer["InputPathsMap"])
    assert '"<' not in template

    # Each path is a plain string in a real alarm event (never an object or array).
    for var, path in transformer["InputPathsMap"].items():
        assert isinstance(_json_path(SAMPLE_ALARM_EVENT, path), str), f"{var} -> {path}"

    task_def = [
        r
        for r in _resources(templates["MiniDebugAssist-Agent"], "AWS::ECS::TaskDefinition")
        if r["Properties"].get("Family") == "mini-debug-assist-agent"
    ][0]
    container_names = {c["Name"] for c in task_def["Properties"]["ContainerDefinitions"]}

    assert list(overrides) == ["containerOverrides"]
    (override,) = overrides["containerOverrides"]
    assert override["name"] in container_names
    env = override["environment"]
    assert all(set(e) == {"name", "value"} for e in env)
    assert all(isinstance(e["value"], str) for e in env), env
    values = {e["name"]: e["value"] for e in env}
    assert values == {
        "ISSUE_SOURCE": "alarm",
        "ALARM_NAME": ERROR_ALARM,
        "ALARM_STATE": "ALARM",
        "ALARM_REASON": SAMPLE_ALARM_EVENT["detail"]["state"]["reason"],
        "ALARM_TIME": "2026-10-06T16:05:00Z",
        "ALARM_REGION": "us-east-1",
        "ALARM_ACCOUNT": "123456789012",
        "ALARM_EVENT_ID": SAMPLE_ALARM_EVENT["id"],
    }
    # ECS caps container overrides at 8192 characters
    assert len(json.dumps(overrides)) < 8192


def test_cli_rebuilds_the_alarm_event_from_rendered_env(synth, monkeypatch):
    """The env vars EventBridge sets are enough for cli.py and the dedup key."""
    from agent.cli import ALARM_ENV_FIELDS, load_issue_from_aws
    from agent.dedup import get_error_signature

    templates, _ = synth
    _, overrides = _rendered_overrides(templates)
    env = {e["name"]: e["value"] for e in overrides["containerOverrides"][0]["environment"]}
    assert set(ALARM_ENV_FIELDS) <= set(env), "cli.py reads a variable the rule does not set"

    monkeypatch.delenv("ALARM_EVENT_JSON", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    issue = load_issue_from_aws(None, None)

    assert issue["issue_id"] == f"ALARM-{ERROR_ALARM}-2026-10-06"
    assert issue["alarm_name"] == ERROR_ALARM
    assert issue["alarm_state"] == "ALARM"
    assert issue["alarm_reason"].startswith("Threshold Crossed")
    assert issue["timestamp"] == "2026-10-06T16:05:00Z"
    assert issue["alarm_event_id"] == SAMPLE_ALARM_EVENT["id"]
    assert issue["region"] == "us-east-1"
    assert issue["account"] == "123456789012"
    # No dimensions on the deployed alarm: no narrowing filter, so the log query
    # falls back to level = "ERROR" instead of exception_type = "Unknown".
    assert issue["error_type"] == ""
    assert issue["endpoint"] == ""

    # Dedup key: stable for the same alarm across events, different for another alarm
    sig = get_error_signature(issue["alarm_name"], issue["error_type"], issue["endpoint"])
    monkeypatch.setenv("ALARM_EVENT_ID", "another-event")
    monkeypatch.setenv("ALARM_TIME", "2026-10-06T16:20:00Z")
    again = load_issue_from_aws(None, None)
    assert get_error_signature(again["alarm_name"], again["error_type"], again["endpoint"]) == sig
    assert get_error_signature("other-alarm", "", "") != sig


def test_cli_keeps_the_alarm_event_json_fallback(monkeypatch):
    from agent.cli import alarm_event_from_env, load_issue_from_aws

    assert alarm_event_from_env({}) is None
    assert alarm_event_from_env({"ALARM_EVENT_JSON": json.dumps(SAMPLE_ALARM_EVENT)}) == (
        SAMPLE_ALARM_EVENT
    )
    # Per-field variables win over ALARM_EVENT_JSON when both are present
    both = {"ALARM_NAME": "from-fields", "ALARM_EVENT_JSON": json.dumps(SAMPLE_ALARM_EVENT)}
    assert alarm_event_from_env(both)["detail"]["alarmName"] == "from-fields"

    for name in ("ALARM_NAME", "ALARM_STATE", "ALARM_REASON", "ALARM_TIME", "ALARM_EVENT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ISSUE_SOURCE", "alarm")
    monkeypatch.setenv("ALARM_EVENT_JSON", json.dumps(SAMPLE_ALARM_EVENT))
    issue = load_issue_from_aws(None, None)
    assert issue["alarm_name"] == ERROR_ALARM
    assert issue["metric_name"] == "ErrorCount"
    assert issue["metric_namespace"] == "MiniDebugAssist/Demo"

"""
Test deployment consistency between CDK stacks and scripts.

Prevents drift between infrastructure definitions and operational scripts.
"""

import json
import subprocess

import pytest


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

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

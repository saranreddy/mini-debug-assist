#!/usr/bin/env python3
"""
Post-deployment smoke test for Mini Debug Assist.

Checks:
- Demo app is healthy
- CloudWatch alarm exists
- EventBridge rule exists
- DynamoDB dedup table exists
- ECS task definition exists
"""

import json
import subprocess
import sys
import requests


def check_demo_app():
    """Check if demo app is responding."""
    try:
        result = subprocess.run(
            ["aws", "cloudformation", "describe-stacks",
             "--stack-name", "MiniDebugAssist-DemoAppStack"],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return False, "Demo app stack not found"
        
        stacks = json.loads(result.stdout)["Stacks"]
        if not stacks:
            return False, "Demo app stack not found"
        
        outputs = stacks[0].get("Outputs", [])
        url = None
        
        for output in outputs:
            if output["OutputKey"] == "DemoAppUrl":
                url = output["OutputValue"]
                break
        
        if not url:
            return False, "Demo app URL not found"
        
        # Check health endpoint
        response = requests.get(f"{url}/health", timeout=5)
        if response.status_code == 200:
            return True, f"Demo app healthy at {url}"
        else:
            return False, f"Demo app returned {response.status_code}"
    
    except Exception as e:
        return False, f"Error checking demo app: {e}"


def check_alarm():
    """Check if CloudWatch alarm exists."""
    try:
        result = subprocess.run(
            ["aws", "cloudwatch", "describe-alarms",
             "--alarm-names", "mini-debug-assist-error-alarm"],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return False, "Error querying alarms"
        
        alarms = json.loads(result.stdout).get("MetricAlarms", [])
        if alarms:
            alarm = alarms[0]
            return True, f"Alarm exists (state: {alarm['StateValue']})"
        else:
            return False, "Alarm not found"
    
    except Exception as e:
        return False, f"Error checking alarm: {e}"


def check_eventbridge_rule():
    """Check if EventBridge rule exists."""
    try:
        result = subprocess.run(
            ["aws", "events", "list-rules",
             "--name-prefix", "mini-debug-assist"],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return False, "Error querying rules"
        
        rules = json.loads(result.stdout).get("Rules", [])
        if rules:
            rule = rules[0]
            return True, f"Rule exists (state: {rule['State']})"
        else:
            return False, "Rule not found"
    
    except Exception as e:
        return False, f"Error checking rule: {e}"


def check_dynamodb_table():
    """Check if DynamoDB dedup table exists."""
    try:
        result = subprocess.run(
            ["aws", "dynamodb", "describe-table",
             "--table-name", "mini-debug-assist-dedup"],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return False, "Table not found"
        
        table = json.loads(result.stdout)["Table"]
        return True, f"Table exists (status: {table['TableStatus']})"
    
    except Exception as e:
        return False, f"Error checking table: {e}"


def check_ecs_task_definition():
    """Check if ECS task definition exists."""
    try:
        result = subprocess.run(
            ["aws", "ecs", "describe-task-definition",
             "--task-definition", "mini-debug-assist-agent"],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return False, "Task definition not found"
        
        task_def = json.loads(result.stdout)["taskDefinition"]
        revision = task_def["revision"]
        return True, f"Task definition exists (revision: {revision})"
    
    except Exception as e:
        return False, f"Error checking task definition: {e}"


def main():
    """Run all smoke tests."""
    print("🔥 Mini Debug Assist - Post-Deployment Smoke Test\n")
    
    checks = [
        ("Demo App", check_demo_app),
        ("CloudWatch Alarm", check_alarm),
        ("EventBridge Rule", check_eventbridge_rule),
        ("DynamoDB Table", check_dynamodb_table),
        ("ECS Task Definition", check_ecs_task_definition),
    ]
    
    results = []
    
    for name, check_fn in checks:
        ok, msg = check_fn()
        results.append((ok, name, msg))
        
        if ok:
            print(f"✅ {name}: {msg}")
        else:
            print(f"❌ {name}: {msg}")
    
    # Summary
    print("\n" + "="*60)
    passed = sum(1 for ok, _, _ in results if ok)
    total = len(results)
    
    if passed == total:
        print(f"✅ All checks passed ({passed}/{total})")
        print("\nDeployment looks good! Next steps:")
        print("  1. make trigger-bug  # Trigger a bug to wake the agent")
        print("  2. Watch for PR in your GitHub repo")
        return 0
    else:
        print(f"⚠️  {total - passed} check(s) failed ({passed}/{total} passed)")
        return 1


if __name__ == "__main__":
    sys.exit(main())

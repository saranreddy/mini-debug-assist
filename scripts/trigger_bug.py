#!/usr/bin/env python3
"""
Trigger demo app bug to wake the agent.

Calls the /user/3 endpoint enough times to trip the CloudWatch alarm
(10 errors in 5 minutes), then shows where to watch for the agent's response.
"""

import json
import subprocess
import sys
import time

import requests


def get_demo_app_url():
    """Get demo app URL from CDK outputs."""
    try:
        result = subprocess.run(
            [
                "aws",
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                "MiniDebugAssist-DemoAppStack",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"❌ Could not get stack outputs: {result.stderr}")
            return None

        stacks = json.loads(result.stdout)["Stacks"]
        if not stacks:
            print("❌ Demo app stack not found")
            return None

        outputs = stacks[0].get("Outputs", [])

        for output in outputs:
            if output["OutputKey"] == "DemoAppUrl":
                return output["OutputValue"]

        print("❌ Demo app URL not found in outputs")
        return None

    except Exception as e:
        print(f"❌ Error getting demo app URL: {e}")
        return None


def trigger_errors(url, count=12):
    """Trigger errors by calling /user/3 endpoint."""
    print(f"\n🐛 Triggering {count} errors at {url}/user/3...")

    errors = 0

    for i in range(count):
        try:
            response = requests.get(f"{url}/user/3", timeout=5)
            if response.status_code == 500:
                errors += 1
                print(f"  [{i+1}/{count}] ✅ Error triggered (500)")
            else:
                print(f"  [{i+1}/{count}] ⚠️  Unexpected status: {response.status_code}")
        except Exception as e:
            print(f"  [{i+1}/{count}] ❌ Request failed: {e}")

        # Brief delay between requests
        time.sleep(0.5)

    return errors


def get_agent_task_arn():
    """Get the agent ECS task ARN (if running)."""
    try:
        result = subprocess.run(
            [
                "aws",
                "ecs",
                "list-tasks",
                "--cluster",
                "mini-debug-assist-cluster",
                "--service-name",
                "mini-debug-assist-agent",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return None

        tasks = json.loads(result.stdout).get("taskArns", [])
        return tasks[0] if tasks else None

    except:
        return None


def main():
    """Main entry point."""
    print("🚀 Mini Debug Assist - Bug Trigger")

    # Get demo app URL
    print("\n📍 Finding demo app...")
    url = get_demo_app_url()

    if not url:
        print("\n❌ Could not find demo app URL.")
        print("   Have you run 'make deploy'?")
        return 1

    print(f"   Found: {url}")

    # Trigger errors
    errors = trigger_errors(url)

    if errors < 10:
        print(f"\n⚠️  Only triggered {errors} errors (need 10 for alarm)")
        print("   Alarm may not trigger. Try running again.")
    else:
        print(f"\n✅ Triggered {errors} errors!")

    print("\n⏱️  Alarm threshold: 10 errors in 5 minutes")
    print("   Wait ~1 minute for CloudWatch to evaluate the alarm...")

    # Wait a bit
    time.sleep(10)

    # Check if agent task is running
    print("\n🔍 Checking for agent task...")
    task_arn = get_agent_task_arn()

    if task_arn:
        print(f"   ✅ Agent task running: {task_arn}")
        print("\n📊 View logs:")
        print("   aws logs tail /aws/ecs/mini-debug-assist-agent --follow")
    else:
        print("   ⏳ No agent task running yet (alarm may still be evaluating)")

    print("\n📝 Next steps:")
    print("   1. Check CloudWatch alarm state:")
    print("      aws cloudwatch describe-alarms --alarm-names mini-debug-assist-error-alarm")
    print("   2. Watch ECS tasks:")
    print("      aws ecs list-tasks --cluster mini-debug-assist-cluster")
    print("   3. Watch for PR in your fork:")
    print("      (Check the GitHub repo you configured)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

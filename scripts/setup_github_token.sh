#!/usr/bin/env bash
#
# Setup GitHub token in AWS Secrets Manager for the agent.
#
# Usage:
#   bash scripts/setup_github_token.sh
#

set -e

echo "🔐 Mini Debug Assist - GitHub Token Setup"
echo ""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install it first."
    exit 1
fi

# Get GitHub token
echo "Enter your GitHub personal access token (will be stored in Secrets Manager):"
echo "Scopes needed: repo (full control)"
read -s GITHUB_TOKEN
echo ""

if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ No token provided"
    exit 1
fi

# Target repository. `make deploy` bakes GITHUB_REPO (env var or .env) into the
# agent task, and that is the repo the agent opens PRs in, so default to it and
# warn if a different one is entered (the token must have access to that repo).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_REPO="$(cd "$REPO_ROOT/infra" && python3 -c \
    'from deploy_config import resolve_github_repo; print(resolve_github_repo())' 2>/dev/null || true)"

if [ -n "$DEPLOY_REPO" ]; then
    echo "Target repository [$DEPLOY_REPO from GITHUB_REPO/.env] (Enter to accept):"
    read GITHUB_REPO
    GITHUB_REPO="${GITHUB_REPO:-$DEPLOY_REPO}"
    if [ "$GITHUB_REPO" != "$DEPLOY_REPO" ]; then
        echo ""
        echo "⚠️  You entered $GITHUB_REPO, but make deploy will use GITHUB_REPO=$DEPLOY_REPO."
        echo "   The agent opens PRs in $DEPLOY_REPO. Update GITHUB_REPO in .env to match,"
        echo "   or make sure this token can push to $DEPLOY_REPO."
        echo ""
    fi
else
    echo "Enter the target repository (e.g., saranreddy/mini-debug-assist):"
    read GITHUB_REPO
fi

if [ -z "$GITHUB_REPO" ]; then
    echo "❌ No repository provided"
    exit 1
fi

# Secret name
SECRET_NAME="mini-debug-assist/github-token"

echo ""
echo "Creating/updating secret: $SECRET_NAME"

# Check if secret exists
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" &> /dev/null; then
    echo "Secret exists, updating..."
    aws secretsmanager put-secret-value \
        --secret-id "$SECRET_NAME" \
        --secret-string "{\"token\":\"$GITHUB_TOKEN\",\"repo\":\"$GITHUB_REPO\"}"
else
    echo "Creating new secret..."
    aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "GitHub token and repo for Mini Debug Assist agent" \
        --secret-string "{\"token\":\"$GITHUB_TOKEN\",\"repo\":\"$GITHUB_REPO\"}"
fi

echo ""
echo "✅ Secret stored successfully!"
echo ""
if [ -n "$DEPLOY_REPO" ]; then
    echo "The agent will open PRs in: $DEPLOY_REPO (GITHUB_REPO)"
else
    echo "⚠️  GITHUB_REPO is not set. Add this line to .env before make deploy:"
    echo "    GITHUB_REPO=$GITHUB_REPO"
fi
echo ""
echo "Next step: make deploy"

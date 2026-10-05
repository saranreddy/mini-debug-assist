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

# Get target repository
echo "Enter the target repository (e.g., saranreddy/mini-debug-assist):"
read GITHUB_REPO

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
echo "The agent will open PRs in: $GITHUB_REPO"
echo ""
echo "Next step: make deploy"

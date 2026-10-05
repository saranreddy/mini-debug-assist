"""
Agent configuration.

Maps to Uber's pipeline configs (go/java/web/android/ios) and agent_type parameter.
"""

import os
from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentConfig:
    """
    Configuration for the debug agent.
    
    Uber has 8 agent types across 5 monorepos. We simplify to one agent
    but keep the configuration pattern for learning.
    """
    
    # Execution mode
    mode: Literal["mock", "aws"] = "mock"
    
    # Agent identity (maps to Uber's agent_type)
    agent_type: str = "python-web"
    
    # Model configuration (maps to Uber's per-node model assignments)
    # In MOCK mode, these are ignored and fixture responses are used
    # 
    # Model IDs from: https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html
    # As of Oct 2026:
    # - Claude 3.5 Sonnet v2 (latest): anthropic.claude-3-5-sonnet-20241022-v2:0
    # - Claude 3 Opus (latest available Opus): anthropic.claude-3-opus-20240229-v1:0
    # - Cross-region inference profiles: us.{model-id}
    #
    bedrock_region: str = "us-east-1"
    # Sonnet for classify, subagents, validate, create_diff (fast, cost-effective)
    model_classify: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    model_subagent: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    model_validate: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    model_create_diff: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    # Opus for fix (more capable for complex code changes)
    model_fix: str = "us.anthropic.claude-3-opus-20240229-v1:0"
    # Escalation uses same Opus
    model_escalate: str = "us.anthropic.claude-3-opus-20240229-v1:0"
    
    # Turn caps (maps to Uber's max-turn guardrails)
    max_turns_classify: int = 20
    max_turns_subagent: int = 5  # Quick focused analysis
    max_turns_fix: int = 50  # Opus gets 30-50 in Uber's setup
    max_turns_validate: int = 20
    max_turns_create_diff: int = 20
    
    # Skills directory (maps to Uber's marketplace)
    skills_dir: str = "skills"
    
    # GitHub configuration (for create_diff)
    github_token: str | None = None
    github_repo: str | None = None
    
    # Observability
    enable_langsmith: bool = False
    langsmith_project: str = "mini-debug-assist"
    
    @classmethod
    def from_env(cls, mode: str = "mock") -> "AgentConfig":
        """Create config from environment variables."""
        return cls(
            mode=mode,
            agent_type=os.getenv("AGENT_TYPE", "python-web"),
            bedrock_region=os.getenv("AWS_REGION", "us-east-1"),
            model_classify=os.getenv(
                "MODEL_CLASSIFY",
                "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            ),
            model_subagent=os.getenv(
                "MODEL_SUBAGENT",
                "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            ),
            model_fix=os.getenv(
                "MODEL_FIX",
                "us.anthropic.claude-3-opus-20240229-v1:0"
            ),
            model_validate=os.getenv(
                "MODEL_VALIDATE",
                "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            ),
            model_create_diff=os.getenv(
                "MODEL_CREATE_DIFF",
                "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            ),
            model_escalate=os.getenv(
                "MODEL_ESCALATE",
                "us.anthropic.claude-3-opus-20240229-v1:0"
            ),
            github_token=os.getenv("GITHUB_TOKEN"),
            github_repo=os.getenv("GITHUB_REPO"),
            enable_langsmith=os.getenv("LANGSMITH_API_KEY") is not None,
        )


def get_config(mode: str = "mock") -> AgentConfig:
    """Get agent configuration based on mode."""
    return AgentConfig.from_env(mode)

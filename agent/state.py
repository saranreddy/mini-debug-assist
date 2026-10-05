"""
Agent state definition.

In LangGraph, state flows through the pipeline from node to node.
This maps to Uber's "shared state" design principle.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Annotated
from operator import add

from langgraph.graph import add_messages


@dataclass
class SubagentResult:
    """
    Result from a parallel RCA subagent.
    
    Maps to Uber's ~30 subagent types (breadcrumbs, release correlation, etc.)
    """
    subagent_type: str
    hypothesis: str
    confidence: float  # 0.0 to 1.0
    evidence: list[str]
    supporting_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RCAResult:
    """
    Root cause analysis result.
    
    Maps to Uber's structured XML output from the classify/RCA node.
    """
    category: Literal["code_bug", "third_party", "infra", "network", "unknown"]
    requires_code_fix: bool
    confidence: float  # 0.0 to 1.0
    root_cause: str
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FixResult:
    """Result from the fix node."""
    fix_applied: bool
    changes: list[dict[str, str]]  # [{file, diff}, ...]
    mitigation: Optional[str] = None  # e.g., "rollback DISCOUNT_V2 flag"
    requires_approval: bool = False


@dataclass
class ValidationResult:
    """Result from the validate node."""
    passed: bool
    test_output: str
    issues: list[str] = field(default_factory=list)


def _merge_subagent_results(left: dict, right: dict) -> dict:
    """
    Merge subagent results from parallel branches.
    
    Each subagent updates the dict with its own key, so we can safely merge.
    """
    return {**left, **right}


def _merge_turn_counts(left: dict, right: dict) -> dict:
    """
    Merge turn counts from parallel branches.
    
    Each node/subagent updates with its own key.
    """
    return {**left, **right}


@dataclass
class AgentState:
    """
    The state that flows through the LangGraph pipeline.
    
    Uber's architecture:
    - context_collector populates context
    - classify_rca populates rca_result
    - parallel subagents enrich evidence
    - consolidator makes decisions
    - fix populates fix_result
    - validate populates validation_result
    - create_diff creates the PR
    
    Note: subagent_results uses Annotated with merge function to handle
    parallel updates from multiple subagents.
    """
    
    # Input (from issue tracking system)
    issue_id: str
    issue_title: str
    issue_data: dict[str, Any]
    
    # Context (from context_collector - deterministic)
    logs: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    code_context: dict[str, str] = field(default_factory=dict)
    
    # RCA (from classify_rca node)
    rca_result: Optional[RCAResult] = None
    
    # Subagent results (from parallel subagents)
    # Annotated with merge function to handle concurrent updates
    subagent_results: Annotated[dict[str, Any], _merge_subagent_results] = field(default_factory=dict)
    
    # Consolidation decision
    needs_human_escalation: bool = False
    escalation_reason: Optional[str] = None
    
    # Fix (from fix node)
    fix_result: Optional[FixResult] = None
    
    # Validation (from validate node)
    validation_result: Optional[ValidationResult] = None
    validation_attempts: int = 0
    max_validation_retries: int = 3
    
    # Fix history (for retry feedback loop)
    fix_history: list[dict[str, Any]] = field(default_factory=list)  # Previous attempts with failures
    
    # Output (from create_diff node)
    pr_url: Optional[str] = None
    
    # Tracking
    turn_count: Annotated[dict[str, int], _merge_turn_counts] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

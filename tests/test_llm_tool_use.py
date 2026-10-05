"""
Tests for LLM tool-use loop with bounded turns.

Tests mocked Bedrock Converse API responses covering:
- Tool call round-trip
- Hitting turn cap
- Malformed output fallback
"""

import json
from unittest.mock import Mock, patch, MagicMock

import pytest

from agent.config import AgentConfig
from agent.llm import invoke_with_tools, parse_rca_result
from agent.state import RCAResult


class TestToolUseLoop:
    """Test bounded tool-use loop with Bedrock Converse."""
    
    @patch("agent.llm.boto3")
    def test_simple_completion_no_tools(self, mock_boto3):
        """Test simple completion without tool use."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client
        
        # Mock response: immediate end_turn
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Analysis complete: code bug detected"}
                    ]
                }
            },
            "stopReason": "end_turn"
        }
        
        result, turns = invoke_with_tools(
            model_id="test-model",
            region="us-east-1",
            system_prompt="You are a debugger",
            messages=[{"role": "user", "content": [{"text": "Analyze this"}]}],
            tools=None,
            max_turns=5,
            node_name="test",
        )
        
        assert turns == 1
        assert "text" in result
        assert "code bug" in result["text"]
        mock_client.converse.assert_called_once()
    
    @patch("agent.llm.boto3")
    def test_tool_use_round_trip(self, mock_boto3):
        """Test tool use with result feedback."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client
        
        # First call: model wants to use tool
        call1_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool-123",
                                "name": "search_code",
                                "input": {"pattern": "KeyError"}
                            }
                        }
                    ]
                }
            },
            "stopReason": "tool_use"
        }
        
        # Second call: model returns final answer after tool result
        call2_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "text": json.dumps({
                                "category": "code_bug",
                                "confidence": 0.9,
                                "root_cause": "KeyError in user endpoint"
                            })
                        }
                    ]
                }
            },
            "stopReason": "end_turn"
        }
        
        mock_client.converse.side_effect = [call1_response, call2_response]
        
        result, turns = invoke_with_tools(
            model_id="test-model",
            region="us-east-1",
            system_prompt="You are a debugger",
            messages=[{"role": "user", "content": [{"text": "Analyze error"}]}],
            tools=[
                {
                    "toolSpec": {
                        "name": "search_code",
                        "description": "Search code",
                        "inputSchema": {"json": {"type": "object"}}
                    }
                }
            ],
            max_turns=5,
            node_name="test",
        )
        
        assert turns == 2
        assert mock_client.converse.call_count == 2
        
        # Check that tool result was fed back
        second_call_messages = mock_client.converse.call_args_list[1][1]["messages"]
        # Should have: initial user msg, assistant tool use, user tool result, (repeated for second turn)
        assert len(second_call_messages) >= 3
        # Check for toolResult in conversation
        assert any("toolResult" in str(msg) for msg in second_call_messages)
    
    @patch("agent.llm.boto3")
    def test_hit_max_turns(self, mock_boto3):
        """Test hitting max turns cap."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client
        
        # Always return tool_use (never end_turn)
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool-loop",
                                "name": "search_code",
                                "input": {"pattern": "test"}
                            }
                        }
                    ]
                }
            },
            "stopReason": "tool_use"
        }
        
        result, turns = invoke_with_tools(
            model_id="test-model",
            region="us-east-1",
            system_prompt="You are a debugger",
            messages=[{"role": "user", "content": [{"text": "Analyze"}]}],
            tools=[{"toolSpec": {"name": "search_code", "inputSchema": {"json": {}}}}],
            max_turns=3,
            node_name="test",
        )
        
        # Should hit max turns
        assert turns == 3
        assert mock_client.converse.call_count == 3
        
        # Should return forced answer with low confidence
        assert result.get("forced") is True
        assert result.get("confidence", 1.0) < 0.5
    
    @patch("agent.llm.boto3")
    def test_max_tokens_stop(self, mock_boto3):
        """Test handling of max_tokens stop reason."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client
        
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Partial analysis..."}]
                }
            },
            "stopReason": "max_tokens"
        }
        
        result, turns = invoke_with_tools(
            model_id="test-model",
            region="us-east-1",
            system_prompt="Analyze",
            messages=[{"role": "user", "content": [{"text": "Debug this"}]}],
            max_turns=5,
            node_name="test",
        )
        
        assert turns == 1
        # Should force final answer due to max_tokens
        assert "forced" in result or "text" in result


class TestStructuredOutput:
    """Test structured output parsing with validation."""
    
    def test_parse_valid_json_result(self):
        """Test parsing valid JSON RCA result."""
        llm_output = {
            "text": json.dumps({
                "category": "code_bug",
                "requires_code_fix": True,
                "confidence": 0.95,
                "root_cause": "KeyError accessing email field",
                "summary": "Missing email field",
                "evidence": []
            })
        }
        
        result = parse_rca_result(llm_output)
        
        assert result["category"] == "code_bug"
        assert result["confidence"] == 0.95
        assert "email" in result["root_cause"]
        assert result["requires_code_fix"] is True
    
    def test_parse_xml_wrapped_json(self):
        """Test parsing JSON wrapped in XML tags."""
        llm_output = {
            "text": """<result>
{
  "category": "code_bug",
  "requires_code_fix": true,
  "confidence": 0.9,
  "root_cause": "Division by zero",
  "summary": "Math error",
  "evidence": []
}
</result>"""
        }
        
        result = parse_rca_result(llm_output)
        
        assert result["category"] == "code_bug"
        assert result["confidence"] == 0.9
    
    def test_parse_code_block_json(self):
        """Test parsing JSON in markdown code block."""
        llm_output = {
            "text": """Here's my analysis:

```json
{
  "category": "infra",
  "requires_code_fix": false,
  "confidence": 0.7,
  "root_cause": "Database timeout",
  "summary": "Slow query",
  "evidence": []
}
```"""
        }
        
        result = parse_rca_result(llm_output)
        
        assert result["category"] == "infra"
        assert result["confidence"] == 0.7
    
    def test_parse_malformed_fallback(self):
        """Test fallback for malformed output."""
        llm_output = {
            "text": "The error is definitely a KeyError but I can't format it properly"
        }
        
        result = parse_rca_result(llm_output)
        
        # Should return low-confidence fallback
        assert result["category"] == "unknown"
        assert result["confidence"] <= 0.5
        assert ("parse failure" in result["root_cause"].lower() or 
                "unable" in result["root_cause"].lower() or
                "determine" in result["root_cause"].lower())
    
    def test_parse_forced_answer(self):
        """Test parsing forced answer (hit turn cap)."""
        llm_output = {
            "forced": True,
            "confidence": 0.3,
            "text": "Incomplete analysis"
        }
        
        result = parse_rca_result(llm_output)
        
        assert result["category"] == "unknown"
        assert result["confidence"] == 0.3
        assert ("turn limit" in result["root_cause"].lower() or 
                "incomplete" in result["root_cause"].lower() or
                "analysis" in result["root_cause"].lower())
    
    def test_parse_with_retry(self):
        """Test retry mechanism on parse failure."""
        # First call: malformed
        llm_output = {
            "text": "Not valid JSON"
        }
        
        # Retry function that returns valid result
        def retry_fn():
            return {
                "text": json.dumps({
                    "category": "code_bug",
                    "requires_code_fix": True,
                    "confidence": 0.8,
                    "root_cause": "Fixed on retry",
                    "summary": "Retry success",
                    "evidence": []
                })
            }
        
        result = parse_rca_result(llm_output, retry_fn=retry_fn)
        
        # Should use retry result
        assert result["category"] == "code_bug"
        assert result["confidence"] == 0.8
        assert "retry" in result["root_cause"].lower()


class TestIntegrationWithClassifyNode:
    """Integration tests with classify_rca node."""
    
    @patch("agent.llm.invoke_with_tools")
    def test_classify_rca_with_tool_use(self, mock_invoke):
        """Test classify_rca node with mocked tool use."""
        from agent.nodes.classify_rca import _perform_rca_with_llm
        from agent.state import AgentState
        from agent.config import AgentConfig
        
        # Mock successful RCA with tools
        mock_invoke.return_value = (
            {
                "text": json.dumps({
                    "category": "code_bug",
                    "requires_code_fix": True,
                    "confidence": 0.92,
                    "root_cause": "KeyError in endpoint",
                    "summary": "Missing field",
                    "evidence": []
                })
            },
            3  # 3 turns used
        )
        
        state = AgentState(
            issue_id="TEST-001",
            issue_title="Test error",
            issue_data={"exception_type": "KeyError"},
            logs=[],
            traces=[],
            code_context={},
        )
        config = AgentConfig(mode="aws")
        
        result, turns = _perform_rca_with_llm(state, config)
        
        assert turns == 3
        assert result.category == "code_bug"
        assert result.confidence == 0.92
        mock_invoke.assert_called_once()
    
    @patch("agent.llm.invoke_with_tools")
    def test_classify_rca_hit_turn_cap(self, mock_invoke):
        """Test classify_rca hitting turn cap."""
        from agent.nodes.classify_rca import _perform_rca_with_llm
        from agent.state import AgentState
        from agent.config import AgentConfig
        
        # Mock forced answer (hit cap)
        mock_invoke.return_value = (
            {
                "forced": True,
                "confidence": 0.3,
                "text": "Unable to complete"
            },
            20  # Hit max turns
        )
        
        state = AgentState(
            issue_id="TEST-002",
            issue_title="Complex error",
            issue_data={},
            logs=[],
            traces=[],
            code_context={},
        )
        config = AgentConfig(mode="aws", max_turns_classify=20)
        
        result, turns = _perform_rca_with_llm(state, config)
        
        assert turns == 20
        # Low confidence should trigger escalation
        assert result.confidence < 0.5
        assert result.category == "unknown"

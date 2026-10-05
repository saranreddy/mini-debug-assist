# Learning Path: Mini Debug Assist

This guide breaks down the codebase into **5 study phases**, from simple to complex. Each phase builds on the previous one.

**Time investment**: ~2-4 hours per phase for careful study and exercises.

---

## 📚 Before You Start

### Prerequisites

- Python 3.11+ experience
- Basic understanding of:
  - APIs (REST, FastAPI)
  - Async programming
  - AWS concepts (even if you haven't used them)
  - LLMs and prompting basics

### Setup

```bash
# Install dependencies
make install

# Run tests to see what works
make test

# Start demo app in one terminal
make run-demo

# Try the endpoints
curl http://localhost:8000/health
curl http://localhost:8000/user/1
curl http://localhost:8000/user/3  # This one crashes!
```

---

## Phase 1: Demo App & Bug Patterns

**Goal**: Understand the demo service and the three types of bugs.

### What to Read

1. `demo_app/main.py` - The FastAPI service
2. `demo_app/config.py` - Feature flag integration
3. `tests/test_demo_app.py` - Tests that expose bugs
4. `skills/python-keyerror.md` - Bug pattern documentation

### Key Concepts

- **Structured logging**: Why JSON logs matter for debugging agents
- **Feature flags**: How AppConfig/Flipr enable safe rollouts
- **Test markers**: Using `@pytest.mark.xfail_bug` to document known issues

### Exercises

**Exercise 1.1**: Trigger each bug

```bash
# Start the demo app
make run-demo

# Trigger KeyError
curl http://localhost:8000/user/3

# Trigger performance issue
curl "http://localhost:8000/report?entries=1000"

# Trigger division by zero
export DISCOUNT_V2=on
curl "http://localhost:8000/discount?price=100&user_id=1"
```

Check the logs - see the structured JSON output?

**Exercise 1.2**: Fix one bug manually

Pick the KeyError bug. Fix it in `demo_app/main.py`:

```python
# Change this:
email = user["email"]

# To this:
email = user.get("email", None)
```

Now run `pytest tests/test_demo_app.py::TestUserEndpoint::test_get_user_without_email`

Did the test pass? Remove the `@pytest.mark.xfail_bug` marker.

**Exercise 1.3**: Add a fourth bug

Add a new buggy endpoint:
- Path: `/calculate/{a}/{b}`
- Bug: Doesn't validate that `b != 0` before `a / b`
- Write a failing test for it

### Things to Try

- Add more structured fields to logs (user_id, request_id, etc.)
- Add a second feature flag (EXPERIMENTAL_CACHE)
- Read about Uber's Flipr system: https://www.uber.com/blog/flipr/

---

## Phase 2: Context Collector & Deterministic Processing

**Goal**: Understand how the agent gathers context without using an LLM.

### What to Read

1. `agent/state.py` - How state flows through the pipeline
2. `agent/nodes/context_collector.py` - Deterministic log fetching
3. `tests/fixtures/keyerror_issue.yaml` - Issue fixture format

### Key Concepts

- **Deterministic vs. LLM nodes**: Why not use LLM for everything?
- **Cost optimization**: Pruning logs before they hit the LLM
- **State management**: How LangGraph passes data between nodes

### Uber's Insight

> "We keep context_collector deterministic to avoid LLM cost on MB-to-GB logs."  
> — Uber's approach: prune first, then analyze

### Exercises

**Exercise 2.1**: Trace the data flow

Add print statements to see state at each step:

```python
# In context_collector_node(), add:
print(f"Collected {len(state.logs)} logs")
print(f"Sample log: {state.logs[0] if state.logs else 'none'}")
```

Run: `make run-agent-mock`

Watch the state evolve!

**Exercise 2.2**: Improve log pruning

The current `_prune_logs()` is simple. Enhance it:

1. Filter by log level (keep ERROR/WARN, sample INFO)
2. Remove duplicate messages
3. Keep logs within ±5 minutes of error timestamp

Test with a fixture that has 1000 logs.

**Exercise 2.3**: Add code context

Currently, mock mode returns hardcoded code. Make it dynamic:

1. Parse the stack trace from issue_data
2. Extract file path and line number
3. Read actual file content (from disk in mock mode)
4. Return ±10 lines around the error

### Things to Try

- Implement real CloudWatch Logs Insights query
- Add X-Ray trace fetching (read AWS docs)
- Compare your pruning logic to log sampling strategies

---

## Phase 3: RCA Node & LLM Integration

**Goal**: Understand root cause analysis with Claude.

### What to Read

1. `agent/nodes/classify_rca.py` - RCA with LLM
2. `agent/nodes/consolidator.py` - Decision making
3. `skills/python-keyerror.md` - How skills guide RCA
4. `agent/config.py` - Model selection and turn caps

### Key Concepts

- **Structured output**: Why RCAResult is typed
- **Turn caps**: Uber's main guardrail (20 turns for RCA)
- **Confidence scores**: When to escalate vs. proceed
- **Skills loading**: How domain knowledge helps the LLM

### Uber's Architecture

```
classify_rca (Claude Sonnet, 20 turns)
  ├── Queries: jaeger, logging, crash-analytics MCP
  ├── Loads: agent_type-filtered skills
  ├── Outputs: Structured XML (category, confidence, root_cause)
  └── Fans out to ~30 parallel subagents (breadcrumbs, release correlation, etc.)
```

### Exercises

**Exercise 3.1**: Run real RCA (requires Bedrock access)

```bash
# Set AWS credentials
export AWS_REGION=us-east-1

# Run in real mode
python -m agent.cli --mode aws --issue tests/fixtures/keyerror_issue.yaml
```

Watch the LLM turns in the logs!

**Exercise 3.2**: Adjust confidence threshold

In `consolidator.py`, the threshold is 0.7:

```python
CONFIDENCE_THRESHOLD = 0.7
```

Try changing it to 0.5 or 0.9. How does it affect escalation?

**Exercise 3.3**: Add a new skill

Create `skills/python-typeerror.md`:

```markdown
# Python TypeError Pattern

**Category**: bug-pattern

## Detection
- Exception type: TypeError
- Common causes: Wrong argument types, None where object expected

## Fix Patterns
...
```

Load it in `classify_rca_node()` and see if it helps.

### Things to Try

- Parse the LLM's structured output (currently simplified)
- Implement skill filtering by `agent_type`
- Add parallel subagents (breadcrumb analyzer, release correlator)
- Compare Claude Sonnet vs. Opus behavior

---

## Phase 4: Fix & Validate Loop

**Goal**: Understand iterative fix generation and testing.

### What to Read

1. `agent/nodes/fix.py` - Fix generation with Opus
2. `agent/nodes/validate.py` - Running tests
3. `agent/graph.py` - The retry loop
4. `skills/feature-flag-rollback.md` - Mitigation strategies

### Key Concepts

- **Bounded retries**: Max 3 validation attempts
- **Symptom hiding detection**: Rejecting overly broad try/except
- **Feature flag mitigation**: When to rollback vs. fix code
- **Test-driven validation**: The fix must pass tests

### Uber's Validation Pipeline

```
fix (Claude Opus, 30-50 turns)
  ↓
bazel_test (run tests, check for symptom hiding)
  ↓
[pass] → create_diff
[fail + retries left] → fix (with test output feedback)
[fail + no retries] → escalate
```

### Exercises

**Exercise 4.1**: Watch the retry loop

Add logging to see retries:

```python
# In validate_node()
logger.info(f"Validation attempt {state.validation_attempts}/{state.max_validation_retries}")
```

Force a validation failure to see retries.

**Exercise 4.2**: Improve symptom hiding detection

The current check is basic. Add detection for:

1. Returning default values without logging
2. Catching `Exception` without re-raising
3. Adding `try/except` around the entire function

**Exercise 4.3**: Implement flag rollback

When `fix_node()` detects strong correlation with a feature flag:

1. Propose rollback via `appconfig_flags` MCP
2. Still generate code fix
3. Mark rollback as requiring approval

### Things to Try

- Run real pytest validation (not just mock)
- Add code quality checks (linting, type checking)
- Implement emulator tests (like Uber's mobile validation)
- Compare fix quality: Sonnet vs. Opus

---

## Phase 5: MCP, Skills & Infrastructure

**Goal**: Understand the broader ecosystem.

### What to Read

1. `mcp_servers/*.py` - All MCP server implementations
2. `skills/*.md` - The knowledge marketplace
3. `infra/stacks/*.py` - AWS infrastructure
4. `agent/graph.py` - How everything connects

### Key Concepts

- **MCP protocol**: Why "11 servers, 1 protocol" matters
- **Skills marketplace**: Separating orchestration from domain knowledge
- **Infrastructure as Code**: CDK for reproducible deployments
- **Observability**: Tracing, metrics, and cost tracking

### Uber's Platform

```
Runtime:
  Python → Bazel → PEX → Artifactory → Docker (Claude Code) → K8s

Tools:
  11 MCP servers: jaeger, logging, Sourcegraph, Jira, flipr, incident-data, ...
  
Knowledge:
  ~3,000 skills, ~30 subagents, 8 agent types, 5 monorepos
```

### Exercises

**Exercise 5.1**: Run MCP server standalone

```bash
# Start CloudWatch MCP server
python mcp_servers/cloudwatch_logs.py

# In another terminal, send MCP request (using MCP client)
# (See MCP spec for protocol details)
```

**Exercise 5.2**: Deploy infrastructure

```bash
# Synthesize (doesn't deploy)
make synth

# Review generated CloudFormation
cat infra/cdk.out/MiniDebugAssist-DemoApp.template.json

# Understand:
# - ECS task definition
# - IAM roles (least privilege)
# - AppConfig setup
```

**Exercise 5.3**: Add observability

Implement LangSmith tracing:

```python
# In graph.py
import os
if os.getenv("LANGSMITH_API_KEY"):
    from langsmith import Client
    client = Client()
    # Instrument the graph
```

Track:
- Tokens used per node
- Latency per node
- Cost per run

### Things to Try

- Implement a new MCP server (e.g., Slack notifications)
- Add more skills for different bug patterns
- Deploy to AWS and run end-to-end
- Profile performance: where does time go?
- Estimate cost per run with real Bedrock calls

---

## 🎯 Final Project: Add a New Feature

**Challenge**: Implement one of these features end-to-end:

### Option A: Parallel Subagents

Uber uses ~30 subagents. Implement 3:

1. **Breadcrumb analyzer**: Parse user actions before crash
2. **Release correlator**: Check if issue started with a deploy
3. **Offending commit finder**: Use git blame on the error line

Use LangGraph's `Send()` for parallelism.

### Option B: Human-in-the-Loop UI

Build a simple web UI:

1. List pending agent runs
2. Show RCA + evidence
3. Approve/reject fixes
4. Chat with the agent ("Ask AI")

Use FastAPI + htmx or React.

### Option C: Multi-Language Support

Extend to handle:

1. JavaScript/TypeScript bugs
2. Add skills for JS patterns
3. Update context_collector for JS stack traces
4. Test with a Node.js demo app

---

## 🔍 Comparison Exercise

**After completing all phases**, compare your understanding with Uber's talk:

1. Watch: https://www.youtube.com/watch?v=iVCDIOf7vXw
2. Read: [architecture.md](../uploads/architecture_f30b.md)
3. Compare:
   - What did Uber do that we skipped?
   - What did we do that they didn't mention?
   - What would you change?

---

## 📖 Further Reading

### Papers & Talks

- [LangGraph documentation](https://python.langchain.com/docs/langgraph)
- [MCP specification](https://modelcontextprotocol.io/)
- [Uber's Flipr blog post](https://www.uber.com/blog/flipr/)
- [Uber's Jaeger blog post](https://www.uber.com/blog/distributed-tracing/)

### Related Systems

- GitHub Copilot Workspace
- Cognition Devin
- SWE-agent
- AutoGPT

### Skills to Develop

- Prompt engineering
- LLM evaluation
- Distributed systems debugging
- Infrastructure as Code

---

## ✅ Completion Checklist

Mark each as you complete it:

### Phase 1
- [ ] Triggered all 3 bugs
- [ ] Fixed one bug manually
- [ ] Added a fourth bug
- [ ] Understood structured logging

### Phase 2
- [ ] Traced state through pipeline
- [ ] Improved log pruning
- [ ] Made context_collector dynamic
- [ ] Understood deterministic vs. LLM tradeoffs

### Phase 3
- [ ] Ran agent in mock mode
- [ ] Adjusted confidence threshold
- [ ] Created a new skill
- [ ] (Optional) Ran with real Bedrock

### Phase 4
- [ ] Watched validation retries
- [ ] Enhanced symptom hiding detection
- [ ] Implemented flag rollback check
- [ ] Understood bounded retry pattern

### Phase 5
- [ ] Ran MCP server standalone
- [ ] Synthesized infrastructure
- [ ] Added observability
- [ ] Estimated costs

### Final
- [ ] Completed one of the final projects
- [ ] Compared with Uber's talk
- [ ] Can explain the architecture to someone else

---

**Congratulations!** 🎉

You now understand how Uber's Debug Assist (and similar agentic systems) work. You've learned:

- Fixed plans vs. free-form reasoning
- Deterministic cost optimization
- LLM orchestration with LangGraph
- MCP for tool integration
- Skills marketplaces for domain knowledge
- Infrastructure for agent deployment

**Next steps**:
- Build your own agentic system
- Contribute to open source (LangGraph, MCP servers)
- Apply these patterns to your domain

Happy learning! 🚀

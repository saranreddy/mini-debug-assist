# Skills Marketplace

This directory contains skill files (markdown) that the agent loads at runtime.
Maps to Uber's ~3,000-skill marketplace that teams contribute to.

## How Skills Work

1. **Loading**: Skills are cloned/loaded at agent startup
2. **Scoping**: `agent_type` parameter filters which skills load
3. **Format**: Markdown files with patterns, examples, and guidance
4. **No Code**: Teams contribute debugging knowledge without redeploying the agent

## Skill Types

- **Bug patterns**: Common bugs and how to detect/fix them
- **Fix patterns**: Standard approaches to specific issues
- **Domain knowledge**: Service-specific context
- **Mitigation strategies**: When and how to rollback flags, etc.

## Uber's Approach

Uber has ~30 unique subagent types and ~3,000 skills. Teams contribute
domain knowledge through the marketplace. The agent loads skills filtered
by `agent_type`, so each agent type gets relevant context.

Example plugins: `android-debugger`, `go-debugger`, `wisdom-debugger`

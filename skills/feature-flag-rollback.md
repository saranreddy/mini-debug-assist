# Feature Flag Rollback Mitigation

**Agent Type**: python-web  
**Category**: mitigation  
**Confidence**: high (requires correlation)

## Description

When a bug strongly correlates with a feature flag being enabled,
rolling back the flag can be faster mitigation than code fix.

Maps to Uber's Flipr flag system and agent's rollback capability.

## When to Propose Rollback

### Strong Correlation Indicators

1. **Timing**: Issue started when flag was enabled
2. **Code path**: Stack trace goes through flag-gated code
3. **Logs**: Flag state logged near error
4. **Rollout correlation**: Error rate matches flag rollout %

### Confidence Levels

- **High (>0.9)**: All 4 indicators present → Propose rollback
- **Medium (0.7-0.9)**: 2-3 indicators → Propose rollback + code fix
- **Low (<0.7)**: Only fix code, mention flag as context

## Rollback Process

### Step 1: Verify Correlation

```
Check:
- When was flag enabled?
- When did errors start?
- Do errors only happen when flag=on?
- Is the flag referenced in stack trace?
```

### Step 2: Propose Rollback (Requires Approval)

```
Mitigation: Rollback feature flag

Flag: DISCOUNT_V2
Current value: on
Proposed value: off
Reason: Division by zero in new discount calculation

Correlation evidence:
- Errors started 2 hours after flag enabled (95% correlation)
- Stack trace in flag-gated code path
- 0 errors when flag=off, 127 errors when flag=on
```

### Step 3: Code Fix (Still Required)

Even with rollback, fix the underlying bug:
- Add validation/error handling
- Add tests for edge cases
- Re-enable flag with fix behind new flag

## Uber's Approach

- Fixes ship **gated behind new Flipr flags**
- Creating/deploying flag is human action
- Agent proposes rollback when correlation is strong
- Both rollback AND fix are done (defense in depth)

## Example: Division by Zero

```python
# Original code (buggy when DISCOUNT_V2=on)
if discount_version == "on":
    discount_multiplier = 100 / purchase_count  # Bug: purchase_count can be 0

# Fix (with new gate)
if discount_version == "on" and config.get_flag("DISCOUNT_V2_FIX"):
    if purchase_count > 0:
        discount_multiplier = 100 / purchase_count
    else:
        discount_multiplier = 5  # Default for new users
else:
    # Legacy algorithm
    ...
```

## Validation

After rollback:
- Monitor error rate (should drop immediately)
- Verify user impact reduced
- Check no side effects from flag=off

After fix:
- Test with flag on/off combinations
- Gradual rollout of fix (1% → 10% → 100%)
- Monitor metrics at each stage

## Related Skills

- python-keyerror.md
- feature-flag-patterns.md (if exists)

## References

- Uber Flipr: https://www.uber.com/blog/flipr/
- Feature Flag Best Practices

# Performance: Hot Loop with Recomputation

**Agent Type**: python-web  
**Category**: performance  
**Confidence**: high

## Description

Expensive operations computed repeatedly inside loops.
Classic O(n*m) problem that should be O(n).

## Detection

- Slow endpoint/function performance
- High CPU usage
- Loop with computation that doesn't depend on loop variable
- List comprehensions, function calls, or allocations inside loop

## Common Patterns

### Pattern 1: Recomputing static data

```python
# Bad: all_items recomputed every iteration
for item in items:
    all_items = [x for x in database.get_all()]  # Don't do this!
    if item in all_items:
        process(item)

# Good: compute once
all_items = [x for x in database.get_all()]
for item in items:
    if item in all_items:
        process(item)
```

### Pattern 2: Repeated function calls

```python
# Bad: function called n times
for i in range(n):
    config = get_config()  # Network call or expensive operation
    process(i, config)

# Good: cache result
config = get_config()
for i in range(n):
    process(i, config)
```

### Pattern 3: String concatenation in loop

```python
# Bad: creates new string every iteration (O(n²))
result = ""
for item in items:
    result += str(item) + "\n"

# Good: use list and join (O(n))
parts = []
for item in items:
    parts.append(str(item))
result = "\n".join(parts)
```

## Detection in Code

Look for:
- Variable assignment inside loop that doesn't use loop variable
- Database/API calls inside loop
- List comprehensions that don't use loop variable
- Heavy computations repeated

## Fix Strategy

1. **Identify invariants**: What doesn't change across iterations?
2. **Hoist computations**: Move them before the loop
3. **Cache results**: Store expensive calls
4. **Batch operations**: If calling external service, batch requests

## Validation

- Profile before/after with `cProfile` or similar
- Benchmark with realistic data sizes
- Load test to ensure improvement holds at scale

## Performance Impact

- O(n*m) → O(n+m) typically gives 10-1000x speedup
- Exact impact depends on:
  - Loop iterations (n)
  - Cost of hoisted operation (m)
  - Whether operation hits cache/network/disk

## References

- Python Performance Tips: https://wiki.python.org/moin/PythonSpeed/PerformanceTips
- Big-O Complexity: https://www.bigocheatsheet.com/

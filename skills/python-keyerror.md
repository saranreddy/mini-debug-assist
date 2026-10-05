# Python KeyError Pattern

**Agent Type**: python-web  
**Category**: bug-pattern  
**Confidence**: high

## Description

KeyError occurs when accessing a dictionary key that doesn't exist.
Common in APIs where data schemas are inconsistent or evolving.

## Detection

- Exception type: `KeyError`
- Stack trace shows dict access: `dict[key]` or `dict.__getitem__(key)`
- Often occurs after data changes or schema migrations

## Root Cause Analysis

1. **Schema inconsistency**: Some records have different fields
2. **Missing validation**: No check before accessing key
3. **API contract violation**: Upstream service changed response format
4. **Migration incomplete**: New field not backfilled on old records

## Fix Patterns

### Pattern 1: Use .get() with default

```python
# Before (crashes)
email = user["email"]

# After (safe)
email = user.get("email", None)
# or with validation
email = user.get("email", "noreply@example.com")
```

### Pattern 2: Validate before access

```python
# Before
value = config["required_field"]

# After
if "required_field" not in config:
    logger.error("Missing required field in config")
    raise ValueError("Invalid configuration")
value = config["required_field"]
```

### Pattern 3: Use Pydantic/TypedDict for validation

```python
from pydantic import BaseModel

class UserSchema(BaseModel):
    id: str
    name: str
    email: str | None = None  # Optional field

# Validate early
user = UserSchema(**user_data)
```

## Validation

- Unit test with missing keys
- Integration test with real data samples
- Check logs for similar patterns in other endpoints

## Related Issues

- Missing field validation
- Schema evolution
- API contract management

## References

- Python dict methods: https://docs.python.org/3/library/stdtypes.html#dict
- Pydantic validation: https://docs.pydantic.dev/

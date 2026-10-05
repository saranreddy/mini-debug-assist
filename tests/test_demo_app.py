"""
Tests for the demo application.

These tests expose the planted bugs. They're marked with pytest.mark.xfail
so CI stays green until the agent fixes them.

Maps to Uber's Bazel test validation in the bazel_test node.
"""

import pytest
from fastapi.testclient import TestClient

from demo_app.main import app

client = TestClient(app)


class TestHealthCheck:
    """Basic health check - should always pass."""
    
    def test_health_endpoint(self):
        """Health check should return 200."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestUserEndpoint:
    """Tests for /user/{user_id} endpoint - exposes KeyError bug."""
    
    def test_get_user_with_email(self):
        """Users with email should work fine."""
        response = client.get("/user/1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "1"
        assert data["name"] == "Alice"
        assert data["email"] == "alice@example.com"
    
    @pytest.mark.xfail(reason="BUG: User 3 has no email field, causes KeyError", strict=False)
    def test_get_user_without_email(self):
        """
        BUG TEST: User 3 is missing 'email' field.
        
        Expected behavior: Should return user data with email set to None
        or a default value.
        
        Actual behavior: Raises KeyError and returns 500.
        
        The agent should:
        1. Detect this KeyError in logs
        2. Analyze the code and find user["email"] access
        3. Fix with .get("email") or validation
        4. This test should pass after the fix
        """
        response = client.get("/user/3")
        
        # After fix, this should return 200 with email=None or similar
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "3"
        assert data["name"] == "Charlie"
        # Email could be None, empty string, or a default value
        assert "email" in data
    
    def test_get_nonexistent_user(self):
        """Non-existent user should return 404."""
        response = client.get("/user/999")
        assert response.status_code == 404


class TestReportEndpoint:
    """Tests for /report endpoint - exposes performance bug."""
    
    def test_report_small(self):
        """Small report should work (but slowly)."""
        response = client.get("/report?entries=10")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == 10
        assert "duration_seconds" in data
    
    @pytest.mark.xfail(reason="BUG: Performance issue - recomputes list in loop", strict=False)
    def test_report_performance(self):
        """
        BUG TEST: Report generation is O(n*m) instead of O(n).
        
        Expected behavior: Should complete in reasonable time even for
        larger inputs (say, < 0.5s for 1000 entries).
        
        Actual behavior: Takes excessive time due to recomputation.
        
        The agent should:
        1. Analyze the code and identify the list comprehension in the loop
        2. Move `all_user_ids` computation outside the loop
        3. This test should pass (complete quickly) after the fix
        """
        response = client.get("/report?entries=1000")
        assert response.status_code == 200
        data = response.json()
        
        # After fix, duration should be reasonable
        assert data["duration_seconds"] < 0.5, (
            f"Report took {data['duration_seconds']}s, expected < 0.5s"
        )


class TestDiscountEndpoint:
    """Tests for /discount endpoint - exposes feature flag bug."""
    
    def test_discount_legacy(self):
        """Legacy discount algorithm (DISCOUNT_V2=off) should work."""
        import os
        os.environ["DISCOUNT_V2"] = "off"
        
        response = client.get("/discount?price=150")
        assert response.status_code == 200
        data = response.json()
        assert data["discount_version"] == "off"
        assert data["discount"] == 15.0  # 10% of 150
    
    def test_discount_v2_without_user(self):
        """New discount without user (with flag off by default)."""
        # Note: Testing with DISCOUNT_V2=on requires env setup before app import
        # For simplicity, this test runs with default flag=off
        response = client.get("/discount?price=100")
        assert response.status_code == 200
        data = response.json()
        # With flag off, uses legacy algorithm
        assert data["discount"] == 5.0  # 5% of 100
    
    @pytest.mark.xfail(reason="BUG: Division by zero when DISCOUNT_V2=on and user has 0 purchases", strict=False)
    def test_discount_v2_new_user(self):
        """
        BUG TEST: New discount with user who has 0 purchases causes division by zero.
        
        Expected behavior: Should handle users with 0 purchase history gracefully,
        perhaps giving a default discount or treating them as new users.
        
        Actual behavior: Divides by zero (100 / purchase_count where purchase_count=0).
        
        The agent should:
        1. Identify the division by zero in discount calculation
        2. Correlate it with DISCOUNT_V2 feature flag being 'on'
        3. Either fix the code (add check for zero) or propose flag rollback
        4. This test should pass after the fix
        """
        import os
        import importlib
        
        os.environ["DISCOUNT_V2"] = "on"
        from demo_app import config as demo_config
        importlib.reload(demo_config)
        
        # User '1' has 0 purchases, triggering division by zero
        response = client.get("/discount?price=100&user_id=1")
        
        # After fix, should return 200 with reasonable discount
        assert response.status_code == 200
        data = response.json()
        assert data["discount_version"] == "on"
        assert "discount" in data
        assert data["discount"] >= 0
        
        # Clean up
        os.environ.pop("DISCOUNT_V2", None)
        importlib.reload(demo_config)


# ===== Test Discovery Notes =====
# 
# When pytest runs with `pytest -v`, it will:
# 1. Run all non-xfail tests -> should pass
# 2. Run xfail tests -> expected to fail, reported as XFAIL
# 3. If an xfail test passes, it's reported as XPASS (unexpected pass)
# 
# This keeps CI green while documenting known bugs.
# After the agent fixes a bug, the corresponding xfail marker should be removed.

---
name: run-tests
description: Run the full test suite and provide a summary
---

# Run Tests

1. Run: `python -m pytest tests/ -v --tb=short 2>&1`
2. Summary: how many tests passed/failed/skipped
3. On failure: show only the FAILED/ERROR lines with context
4. Suggest improvements for repeated failures

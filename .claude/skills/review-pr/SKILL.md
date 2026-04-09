---
name: review-pr
description: Thoroughly review a pull request
---

# Review PR <number>

1. Fetch PR diff: `gh pr diff <number>`
2. Fetch PR details: `gh pr view <number> --json title,body,files,reviews`
3. Analyze:
   - Correctness of the implementation
   - Test coverage (are there tests for the changes?)
   - Code style (Ruff-clean, mypy strict, type hints on public functions)
   - HA conventions (async def, no sync I/O, proper entity hierarchy)
   - Potential bugs or edge cases in PID/estimator logic
   - Breaking changes to config flow or subentry schema
4. Write review comments as a markdown list
5. Post comments via: `gh pr review <number> --comment --body "[your review]"`

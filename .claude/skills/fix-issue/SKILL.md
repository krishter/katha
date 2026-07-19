---
name: fix-issue
description: Fix a GitHub issue end-to-end
disable-model-invocation: true
---
Fix the GitHub issue: $ARGUMENTS

1. Run `gh issue view $ARGUMENTS` to get issue details
2. Understand the problem
3. Search codebase for relevant files
4. Implement the fix
5. Write/run tests to verify
6. Ensure linting/type checks pass
7. Commit with a descriptive message
8. Push and open a PR referencing the issue

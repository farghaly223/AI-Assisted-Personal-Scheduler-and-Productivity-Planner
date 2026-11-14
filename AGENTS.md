
# Engineering Rules

## Priorities
- Security and performance come first.
- Avoid overengineering and unnecessary abstractions.
- Preserve the existing architecture unless a change is justified.

## Before Coding
- Inspect only relevant files and dependencies.
- Reuse existing patterns and utilities.
- Do not scan the entire repository for small tasks.
- Give a short plan only for multi-file or risky changes.

## Implementation
- Make the smallest correct change.
- Avoid unrelated refactoring.
- Never weaken authentication, authorization, RLS, validation, or tests to make something work.
- Never expose secrets or use privileged credentials for normal runtime operations.

## Verification
- Run targeted tests first.
- Run broader tests when the change affects shared behavior.
- Verify security and performance implications.
- Report exact files changed, tests run, and remaining risks.

## Communication
- Be concise.
- Do not repeat repository information already established.
- Do not generate long explanations unless requested.
- Ask before destructive operations or production database changes.

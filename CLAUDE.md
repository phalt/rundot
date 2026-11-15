# CLAUDE.md

## Workflow

### Workflow for planned tasks

1. Read the plan, spec and todos, then present a short plan. STOP and wait for my approval.
2. Implement test-first in red-green slices. Keep 100% line and branch coverage.
3. Only tick checkboxes or approval boxes when I say so, or after CI is confirmed green.

### Finishing a task

Once I've approved the work on a task's PR, do all of this without asking again:

1. Wait for the PR's CI to pass. If it fails, fix it and wait again.
2. Tick that task's checkboxes in the todo file on the PR branch (CI is now confirmed green), push, and wait for CI again.
3. Merge the PR.
4. Wait for CI on `main` after the merge. If it fails, fix it on a new branch and PR, and tell me.
5. Give me a copy-pastable prompt for the next task in the todo, in one fenced code block, in the same shape as the prompt I gave for this one: what to read first, the task's scope, and the rules (test-first, coverage, the make targets, CHANGELOG, small commits, don't tick boxes, push and open a PR).

# Engineering workflow

For coding and implementation tasks:

1. Inspect the repository, requirements, existing instructions, and current Git changes before modifying files.
2. Delegate implementation to the `implementer` agent.
3. Wait for implementation to finish before starting an independent review.
4. Delegate review to the `reviewer` agent.
5. Inspect the actual review findings and validate them against the requirements and code.
6. If material defects are found, send actionable findings to `implementer`.
7. Have `implementer` repair valid issues, add regression tests, and rerun relevant checks.
8. Ask `reviewer` to review the updated changes again.
9. Allow at most three repair rounds after the initial implementation.
10. Before completion, run the authoritative project test suite and relevant lint, formatting, and type checks when the project provides them.
11. Inspect the final diff and ensure that no unrelated changes were introduced.
12. Report actual check results. Do not fabricate success, remove assertions, or weaken tests to obtain a pass.
13. If the loop cannot converge, stop and explain the unresolved blockers.
14. Do not push, deploy, publish, delete user data, or perform destructive operations without explicit authorization.

Routine code edits and repairs do not require an extra approval request. Follow the active sandbox, permission, and authorization controls. Agent instructions do not grant additional filesystem permissions or override those controls.

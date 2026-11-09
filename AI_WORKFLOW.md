# AI-Assisted Development Workflow

Work on one small, independently verifiable task at a time. Do not start the next task until the current task is inspected, tested, and documented. Ask for approval at any explicit project approval gate.

## Required Sequence

1. **Specify:** Restate the task, scope, acceptance criteria, and files likely to change. Identify relevant existing conventions and risks. Keep the change limited to the approved task.
2. **Implement:** Make the smallest complete change. Preserve unrelated work. Do not add dependencies, alter architecture, or broaden scope without a clear need and approval where required.
3. **Inspect:** Review the diff and affected behavior for correctness, security, unintended changes, and consistency with the project specification.
4. **Test:** Run the narrowest relevant tests first, then any broader required checks. Report commands and actual results; never claim an unrun test passed. If tests cannot run, explain why.
5. **Update Documentation:** Update the relevant specification, setup instructions, task checklist, and progress notes so they match the implemented state.
6. **Commit:** Once the task is verified and documentation is current, create a focused commit with a clear message. Do not commit secrets, generated clutter, or unrelated work. If the user has not authorized committing or the environment cannot commit, leave changes uncommitted and report that clearly.

## Working Rules

- Keep each task small enough to review and test independently.
- Treat security requirements in `PROJECT_SPEC.md` as acceptance criteria, especially backend authorization and ownership checks.
- Keep each feature's records as the single source of truth; reports must derive from feature-owned data rather than duplicated mock summaries. Do not call CSV export complete until it exports actual application state.
- When a reporting rule is unspecified, record the open decision and define/test it before implementing the report. In particular, do not double-count loan principal as an expense or invent repayment accounting behavior.
- Do not infer completion from plans or code presence; verify actual behavior with tests or clearly report the remaining gap.
- Preserve existing user changes. Do not overwrite or delete useful work without explicit approval.
- Do not install packages, deploy, upload project files, or commit secrets unless a later task explicitly calls for an allowed action.
- At task end, summarize changed files, validation results, known issues, and the exact next task. Stop at approval gates and wait for the user's direction.
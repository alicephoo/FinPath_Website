# AI-Assisted Development Workflow

Work on one small, independently verifiable task at a time. Do not start the next task until the current task is inspected, tested, and documented. Ask for approval at any explicit project approval gate.

## Relationship to `AGENTS.md`

[`AGENTS.md`](AGENTS.md) is the repository-level source for project context, documentation precedence, the six agent roles, and safety rules. This file describes how a task moves through the team; `AGENTS.md` defines who performs each role and which actions require human approval.

- The Coordinator / Orchestrator applies this sequence, checks `PROJECT_SPEC.md`, and issues a delegation packet to the relevant specialist role.
- Implementation, review, and documentation handoffs use the packet and handoff template in `AGENTS.md`.
- Small, low-risk tasks may use a simplified single-agent workflow: one agent performs the same six steps and still reports evidence and respects every approval gate.
- This is a documented collaboration process, not automated orchestration. Agents do not spawn, schedule, or approve one another automatically.

## Task Tracking

The active tracker is the public [FinPath — Development Tracker](https://github.com/users/alicephoo/projects/1). `TASKS.md` is historical and must not be used to signal current task status.

## Required Sequence

1. **Specify:** Restate the task, scope, acceptance criteria, and files likely to change. Confirm the matching board item, its acceptance criteria, and the responsible agent role; create an item only with approval. Identify relevant existing conventions and risks. Keep the change limited to the approved task.
2. **Implement:** Make the smallest complete change. Move the board item to `In Progress` before editing. Preserve unrelated work. Do not add dependencies, alter architecture, or broaden scope without a clear need and approval where required.
3. **Inspect:** Review the diff and affected behavior for correctness, security, unintended changes, and consistency with the project specification.
4. **Test:** Run the narrowest relevant tests first, then any broader required checks. Report commands and actual results; never claim an unrun test passed. If tests cannot run, explain why.
5. **Document evidence:** Update the relevant specification, setup instructions, task checklist, and progress notes so they match the implemented state. Record actual implementation and verification evidence on the board item, and move it to `Done` only after verification.
6. **Commit (only with explicit human approval):** Once the task is verified and documentation is current, and the human owner has approved committing, create a focused commit with a clear message. Do not commit secrets, generated clutter, or unrelated work. If the user has not authorized committing or the environment cannot commit, leave changes uncommitted and report that clearly.

## Working Rules

- Keep each task small enough to review and test independently.
- Treat security requirements in `PROJECT_SPEC.md` as acceptance criteria, especially backend authorization and ownership checks.
- Keep each feature's records as the single source of truth; reports must derive from feature-owned data rather than duplicated mock summaries. Do not call CSV export complete until it exports actual application state.
- When a reporting rule is unspecified, record the open decision and define/test it before implementing the report. In particular, do not double-count loan principal as an expense or invent repayment accounting behavior.
- Do not infer completion from plans or code presence; verify actual behavior with tests or clearly report the remaining gap.
- Preserve existing user changes. Do not overwrite or delete useful work without explicit approval.
- Do not install packages, deploy, upload project files, or commit secrets unless a later task explicitly calls for an allowed action.
- Follow `AGENTS.md` for role boundaries, documentation precedence, and approval gates. When a task spans frontend and backend, freeze the API contract before parallel implementation.
- Check the public tracker before starting and update the board item's status as work progresses; never mark an item `Done` without verified acceptance criteria.
- Never fabricate task status, assignments, comments, or evidence. `TASKS.md` is historical; do not use it as the live tracker.
- At task end, summarize changed files, validation results, known issues, and the exact next task. Stop at approval gates and wait for the user's direction.

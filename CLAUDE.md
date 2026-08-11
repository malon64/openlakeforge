# CLAUDE.md

Read [AGENTS.md](AGENTS.md). It is the single source for how to work in this
repository: project context, orientation reading order, repository map,
architectural rules, coding standards, the gates that must pass, and guardrails.

This file exists only so Claude Code finds the guide by its conventional name.
The rules are deliberately not duplicated here — two copies drift apart, and a
stale copy is worse than none.

## Quick reference

```bash
make release-check    # every static gate, also enforced on each pull request
make local-up         # bring up the local kind stack
make local-e2e        # full runtime verification
```

Before writing code: read `AGENTS.md`, then the issue you are working on in
full (`gh issue view <n>`) — issue bodies carry verified file and line
references that a summary will not.

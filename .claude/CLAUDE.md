# Bull-Bot — Claude session standing rules

## Rule 1: Always use Superpowers. Always.

First action of EVERY session in this repo, before answering any question or running any tool:

1. List the skills in `~/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/skills/`.
2. Read `using-superpowers/SKILL.md` (or its equivalent index).
3. Identify which skills apply to the task (e.g. `systematic-debugging`, `verification-before-completion`, `test-driven-development`, `writing-plans`).
4. Read those skill files BEFORE coding, debugging, or reporting.

This rule was set by Dan on 2026-05-12 after a mentor run that skipped Superpowers and produced wrong claims. There is no scenario where it's okay to skip this step. If the task feels small, that is exactly when the rule matters most.

## Rule 2: Source of truth beats intermediate reports.

Before saying "X doesn't exist" or "Y has no data," check the actual file / DB / path. Generated reports go stale; markdown summaries lie; the SQLite DB and the git log do not. Inspect the data, not the readout of the data.

## Rule 3: Plain-language status for Dan.

Dan is a PM, not a backend engineer. Lead with what trades happened, what dollars moved, what stopped working. Save filesystem details, git plumbing, and column names for the appendix or for when he asks.

## Project entry points

- Daily mentor prompt: `.mentor/DAILY_PROMPT.md`
- Current narrative: `.mentor/STATE.md`
- Backlog: `.mentor/BACKLOG.md`
- Lessons log: `.mentor/LESSONS.md`
- Recent handoff context: `.claude/handoff.md`
- Live DB: `cache/bullbot.db` (ground truth for trades / strategies / bars)

# Hackathon collaboration

What it is: the fast lane-based collaboration rules for hackathon
implementation. Agents own most implementation; humans own direction, review,
and merge decisions. One writer per worktree/path at a time.

## Collaboration contract

- Agents do the implementation work; the human sets direction and stays the
  decision authority on scope, product, and merge calls.
- One writer per worktree/path: never edit the same paths from two lanes at
  once. If paths overlap, the lane owners agree on a split first.
- Every lane branches from a pushed baseline (`git pull` before branching)
  and preserves another lane's WIP: never delete, overwrite, or absorb
  another lane's uncommitted or unpushed work.
- Every task change is tested, reviewed, committed, and pushed before handoff.
  No handoff on dirty or unpushed state.
- A handoff states: changed paths, exact test/build evidence (command plus
  result), commit SHA, and remaining hardware/manual gaps.
- The integration owner merges only reviewed and pushed work, in clean merge
  order (baseline first, then lanes smallest-first). Unreviewed or unpushed
  work waits.
- All Pi subagents default to Muse Spark unless the user explicitly overrides
  the model choice.
- Never `git reset`, `force-push`, or commit another lane's changes without
  explicit instruction from that lane's owner.

## Isolated worktrees and merge order

- Teammate setup: `git pull`, then one worktree per lane
  (`git worktree add ../<lane> -b <lane-branch>`), one task per worktree.
- Keep each worktree clean: commit and push in-lane before switching lanes.
- Clean merge order: integration owner pulls the baseline, merges each
  reviewed/pushed lane branch smallest-first, runs the verification gate
  after each merge, and pushes before merging the next lane.

## How to verify a handoff

- `git status --porcelain` is empty and `git log -1 --format=%H` matches the
  SHA stated in the handoff.
- Changed paths from the handoff exist in that commit
  (`git show --stat <SHA>`).
- Re-run the stated test/build command and confirm the same result.
- Remaining hardware/manual gaps are listed explicitly (e.g. "not run on
  device"); nothing claimed without evidence.

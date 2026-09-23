# Architecture

## Goal

Auto Checkpoint preserves a **known task file set** while refusing to absorb
unclassified working-tree state.

Its boundary is:

```text
dirty working tree
      |
      v
explicit classification
      |
      +-- task files ------> eligible for checkpoint/commit
      +-- preserved WIP ---> untouched
      +-- generated -------> classified but not committed
      +-- unknown ---------> BLOCK
      |
      v
security + Git guards
      |
      v
validation commands
      |
      v
private checkpoint
      |
      v
explicit git add -- <task files only>
      |
      v
commit
      |
      v
push + remote verification
      |
      +-- PASS
      |
      +-- FAIL -> private retry queue
```

## Quality boundary

When a `--quality-report` is supplied, Auto Checkpoint verifies the report
before the private checkpoint is created.

```text
working tree
   |
   v
Quality Gate
   |
   +-- PASS report + Git fingerprint
   |
   v
Auto Checkpoint verify-report
   |
   +-- stale/fail -> BLOCK
   |
   v
private checkpoint -> commit -> push
```

Auto Checkpoint delegates fingerprint semantics to the installed Quality Gate
verifier instead of reimplementing that policy.

The Quality Gate report is evidence for the pre-checkpoint state. The later
checkpoint manifest stores the verified fingerprint so the relationship remains
auditable after the commit changes HEAD.

## Why checkpoint before commit

The private checkpoint is created before Git staging/commit. It preserves the
authorized file contents and their SHA-256 values even if the later push fails.

The checkpoint is outside the repository so it is not accidentally committed
with the task.

## State layout

```text
<state-root>/
├── pending-push.jsonl
└── <repository>/
    └── <UTC timestamp>/
        ├── manifest.json
        ├── git-state.txt
        └── files/
            └── <authorized task paths>
```

Default state follows XDG:

```text
$XDG_STATE_HOME/auto-checkpoint
```

or:

```text
~/.local/state/auto-checkpoint
```

## Git safety model

The operation blocks when it detects:

- a repository path that is not the Git top-level;
- pre-existing staged changes;
- merge/rebase/cherry-pick/revert state;
- detached HEAD;
- unexpected upstream;
- a remote branch not ancestral to local HEAD.

Only the authorized task files are passed to:

```text
git add -- <file...>
```

No `git add .`, `git add -A` or force push belongs in the design.

## Push failure

A successful local commit is preserved even when the remote push fails.

The queue entry records only what retry needs:

- repository;
- remote name;
- branch;
- commit;
- private manifest path.

`retry-push` verifies the remote head before marking the checkpoint synced.

## Recovery

Recovery is a separate command family, never an automatic side effect.

- `list` inventories checkpoints;
- `inspect` shows the manifest;
- `verify` recomputes SHA-256;
- `restore-files` copies selected checkpoint files;
- `recover-commit` creates a recovery branch.

See `docs/RECOVERY.md`.

## Offsite

Offsite is optional and delegated to an existing operator-configured rclone.

The tool performs:

```text
copy
check --one-way
copy final manifest
check --one-way
```

This is deliberately different from destructive synchronization.

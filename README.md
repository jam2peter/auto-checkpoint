# Auto Checkpoint

**Auto Checkpoint** is a fail-closed Git work preservation tool.

It creates a private checkpoint of explicitly authorized task files, validates
them, commits only those files, pushes without force, and keeps recovery
evidence outside the repository.

> Status: `v0.1-beta` candidate.

## Why

A common automation failure mode is to turn "save my work" into:

```text
git add -A
git commit
git push
```

That is unsafe when a working tree contains unrelated WIP, generated files,
credentials, staged changes, a pending merge/rebase, or a divergent remote.

Auto Checkpoint does the opposite: **unknown state blocks the operation**.

## Core contract

A checkpoint succeeds only when:

- every task file is explicitly listed with `--file`;
- every remaining changed path is explicitly classified as preserved WIP or
  generated;
- no selected file looks private or secret;
- the Git index is clean before the operation;
- no merge/rebase/cherry-pick/revert is pending;
- HEAD is attached to a branch;
- the configured remote is compatible with the current branch;
- validations pass;
- only the declared task files are staged;
- the resulting push can be verified, or the failed push is queued for retry.

No force-push is used.

## Install

Requires Python 3.11+ and Git.

```bash
git clone https://github.com/jam2peter/auto-checkpoint.git
cd auto-checkpoint
python3 -m pip install .
```

Then:

```bash
auto-checkpoint --help
```

## Create a checkpoint

```bash
auto-checkpoint checkpoint \
  --repo /path/to/repository \
  --task-id TASK-123 \
  --message "finish parser validation" \
  --file src/parser.py \
  --file tests/test_parser.py \
  --preserve-wip notes/experiment.txt \
  --validate "python3 -m unittest discover -s tests -v"
```

The commit message becomes:

```text
task(TASK-123): finish parser validation
```

## Private state

By default, checkpoint state is stored under:

```text
$XDG_STATE_HOME/auto-checkpoint/
```

or, when `XDG_STATE_HOME` is unset:

```text
~/.local/state/auto-checkpoint/
```

Each checkpoint contains:

```text
<state>/<repo>/<UTC>/
├── manifest.json
├── git-state.txt
└── files/
```

The manifest records:

- SHA-256 per preserved task file;
- branch and pre-checkpoint HEAD;
- validation results;
- sanitized remote URL;
- commit hash;
- local/remote sync state;
- optional offsite verification state.

Checkpoint files and manifests are private mode where the platform supports
POSIX permissions.

## Push retry

A failed push does not discard the local commit.

It records a private retry queue:

```bash
auto-checkpoint retry-push
```

The queue is removed only when every pending commit is verified at the remote.

## Recovery

Recovery is explicit.

```bash
auto-checkpoint recovery list
auto-checkpoint recovery inspect CHECKPOINT
auto-checkpoint recovery verify CHECKPOINT
auto-checkpoint recovery restore-files CHECKPOINT \
  --target /path/to/restore \
  --file src/parser.py
auto-checkpoint recovery recover-commit CHECKPOINT \
  --repo /path/to/repository
```

`restore-files` refuses to overwrite an existing destination unless
`--overwrite` is given explicitly.

`recover-commit` creates a recovery branch. It does not switch the current
working tree.

See [Recovery](docs/RECOVERY.md).

## Optional offsite copy

If `rclone` is already configured by the operator:

```bash
auto-checkpoint checkpoint \
  ... \
  --offsite BackupRemote:path/checkpoints
```

Auto Checkpoint uses only:

```text
rclone copy
rclone check --one-way
```

It does not use `rclone sync`.

rclone configuration and credentials are never copied into the checkpoint by
the tool.

## What it intentionally does not do

Auto Checkpoint does not:

- stage the whole repository;
- guess which WIP belongs to the task;
- force-push;
- auto-resolve merge conflicts;
- overwrite recovery targets by default;
- auto-install system services;
- discover or configure cloud credentials;
- act as a general backup daemon.

## JamPeter Ops Stack

Auto Checkpoint is the **Preserve** layer of the
[JamPeter Ops Stack](https://github.com/jam2peter/ops-stack):

```text
Provision -> Govern -> Schedule -> Execute
                              |
                              v
                         Preserve
                      Auto Checkpoint
                              |
                              v
                           Deploy
                              |
                              v
                           Verify
```

It protects validated work before release. A successful checkpoint does not
itself authorize deployment.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The test suite uses only temporary Git repositories and local bare remotes.

## Security

See [Security](docs/SECURITY.md) and
[Architecture](docs/ARCHITECTURE.md).

## Origin

Auto Checkpoint is a clean public extraction of a checkpoint capability proven
in the JamPeter environment. The public product uses generic XDG/home paths,
synthetic tests and no private runtime configuration.

## License

MIT

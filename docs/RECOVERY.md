# Recovery

Recovery is always explicit.

## List

```bash
auto-checkpoint recovery list
```

Outputs checkpoint identifier, repository name, task ID and commit hash.

## Inspect

```bash
auto-checkpoint recovery inspect CHECKPOINT
```

Shows the private manifest.

## Verify file integrity

```bash
auto-checkpoint recovery verify CHECKPOINT
```

Recomputes SHA-256 for every preserved file.

Expected:

```text
SHA256_VALIDATION=PASS
```

## Restore selected files

```bash
auto-checkpoint recovery restore-files CHECKPOINT \
  --target /path/to/recovery-directory \
  --file src/example.py
```

The command refuses to overwrite an existing target path.

Only when explicitly intended:

```bash
auto-checkpoint recovery restore-files CHECKPOINT \
  --target /path/to/recovery-directory \
  --file src/example.py \
  --overwrite
```

## Recover a commit

```bash
auto-checkpoint recovery recover-commit CHECKPOINT \
  --repo /path/to/repository
```

This creates:

```text
recovery/<task-id>-<checkpoint-id>
```

at the recorded commit.

It does not switch branches or modify the working tree.

## Push retry is not recovery

A push failure after a successful commit is handled separately:

```bash
auto-checkpoint retry-push
```

The local commit and checkpoint remain intact while remote synchronization is
pending.

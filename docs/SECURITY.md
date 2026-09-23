# Security

## Fail-closed principle

Auto Checkpoint assumes that ambiguity is a reason to stop.

It will not silently infer that an unknown modified file belongs to the task.

## Secret/private file gates

Selected task files are blocked when:

- their filename is on the private-name blocklist;
- their filename contains sensitive markers such as token, credential, OAuth,
  private key or client secret;
- an existing regular file has mode `0600`;
- the file is a symlink or non-regular file;
- its content matches common private-key/token/password patterns.

These checks are defense in depth, not a complete secret scanner. Operators
must still avoid selecting sensitive files.

## Remote sanitization

Remote URLs stored in manifests are sanitized.

Credentials/userinfo in HTTPS-style URLs are removed. SSH-like
`user@host:path` strings are reduced to `host:path`.

## Shell validations

`--validate` commands are intentionally executed through the shell.

They are **trusted project configuration/operator input**, not untrusted remote
input. Do not feed arbitrary user-controlled strings into `--validate`.

## Git protection

The tool blocks:

- pre-existing staged changes;
- pending merge/rebase/cherry-pick/revert operations;
- detached HEAD;
- incompatible upstream;
- remote divergence.

It never force-pushes.

## Recovery protection

`restore-files` refuses overwrite by default.

`recover-commit` creates a branch and never checks it out automatically.

## Offsite credentials

Auto Checkpoint never reads, copies or stores rclone configuration by itself.

If offsite backup is enabled, rclone credential custody remains the operator's
responsibility.

## Logging

The CLI prints operational state such as checkpoint path, commit hash and sync
status.

It must not print:

- secret values;
- rclone credentials;
- Authorization headers;
- unsanitized credential-bearing remote URLs.

## Security reports

Do not publish credentials or exploit material in a public Issue. Use the
maintainer's private contact/security channel for sensitive reports.

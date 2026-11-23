# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.1.x | yes |
| < 1.1 | no |

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting
(**Security → Report a vulnerability**) on this repository. Include the affected
version, a minimal reproduction and the impact you observed. You will get an answer
within a week.

## Deployment notes

- The example tokens in `compose.yaml` are for local use only. Set your own with
  `RUBATO_CLIENTS` (for example in a git-ignored `.env`). The service logs a warning and
  the panel shows an alert while the example tokens are in use.
- `RUBATO_TEST_CLOCK=1` enables the reset and clock endpoints. Use it only for tests and
  demos; it is off by default.
- The service binds to `127.0.0.1` in Compose. Put a TLS-terminating proxy in front of it
  before exposing it to a network.
- Adapters send planning metadata only: task class, model and token counts. Prompts,
  documents and personal data must never be sent to Rubato.

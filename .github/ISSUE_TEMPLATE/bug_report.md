name: Bug Report
description: Report a bug or unexpected behavior
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to report an issue.

  - type: input
    id: version
    attributes:
      label: Coworker Version
      description: What version of Coworker are you using?
      placeholder: e.g. v0.1.0

  - type: dropdown
    id: platform
    attributes:
      label: Platform
      options:
        - macOS (Apple Silicon)
        - macOS (Intel)
        - Windows 10+ (x64)
        - Linux (AppImage)
      multiple: true

  - type: textarea
    id: steps
    attributes:
      label: Steps to Reproduce
      description: How can we see what you're seeing?
      placeholder: |
        1. Open Coworker
        2. ...

  - type: textarea
    id: expected
    attributes:
      label: Expected Behavior
      description: What did you expect to happen?

  - type: textarea
    id: actual
    attributes:
      label: Actual Behavior
      description: What actually happened?

  - type: textarea
    id: logs
    attributes:
      label: Relevant Logs / Trace
      description: If applicable, paste output from Settings → Runtime Observability or system logs.
      render: text

  - type: textarea
    id: additional
    attributes:
      label: Additional Context
      description: Any other context about the problem?

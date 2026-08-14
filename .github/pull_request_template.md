name: Pull Request
description: Submit a pull request
body:
  - type: markdown
    attributes:
      value: |
        Thanks for submitting a PR! Please review the checklist below before submitting.

  - type: input
    id: ticket
    attributes:
      label: Related Issue
      description: Link to the related issue (if any).
      placeholder: "Fixes #123"

  - type: textarea
    id: changes
    attributes:
      label: Changes
      description: Briefly describe what this PR changes.

  - type: textarea
    id: testing
    attributes:
      label: Testing Done
      description: How did you verify your changes?

  - type: textarea
    id: checklist
    attributes:
      label: Checklist
      description: Ensure your PR meets these requirements.
      value: |
        - [ ] Code builds without errors (`npm run build`)
        - [ ] Python compiles (`backend/venv/bin/python -m compileall backend/main.py backend/coworker`)
        - [ ] TypeScript type-checks pass (`cd frontend && npx tsc --noEmit`)
        - [ ] No trailing whitespace or line-end issues (`git diff --check`)
        - [ ] Commits are clean and messages are descriptive

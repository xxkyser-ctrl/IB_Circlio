# Repository instructions

- NEVER add `Co-authored-by: Copilot`, `Co-authored-by: GitHub Copilot`, `copilot@github.com`, or any other Copilot attribution to Git commit messages.
- Apply this rule to every commit, pull request, update, refactor, bug fix, feature, and automated change.
- Before every commit, inspect the complete proposed commit message and remove any Copilot attribution.
- After every commit, verify the message with `git log -1 --format=%B` and confirm that it contains no Copilot attribution.
- The checked-in `.githooks/commit-msg` hook removes Copilot co-author trailers. Enable it in each clone with `git config core.hooksPath .githooks`.

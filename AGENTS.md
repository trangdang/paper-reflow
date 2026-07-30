# Agent Rules

- Think before coding: Ask and clarify intent if any requirements are ambiguous; do not assume.
- Keep code minimal and focused: Write the minimum required code to solve the exact problem without speculation.
- Edit surgically: Touch only the files and lines that matter; do not refactor unrelated code.
- Goal-driven execution: Define strict success criteria upfront and loop until tests or verification pass.

- Never run `git push`, `gh pr create`, or any remote git operations.
- Do not access files outside this repo, with the exception of `~/.cache/uv`.
- Explain planned changes before editing.
- Never modify secrets or environment files.
- Leave existing comments alone, unless it's no longer relevant.

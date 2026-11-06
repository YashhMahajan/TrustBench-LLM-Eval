"""Continuous integration: check a change in CI and report it on the pull request.

  comment.py   Builds the Markdown comment: gate verdict, pass rates, slices, cost, top failures. Pure functions.
  github.py    Posts that comment, or updates the one it posted before. Uses only GITHUB_TOKEN.
  check.py     `trustbench ci check`: run, gate, comment and post in one step. The GitHub Action calls this.

The reusable Action is action.yml in the repository root. The workflow for this repository is .github/workflows/ci.yml.
"""

# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues:
`setupdata/gmsh-doc-cn`.

Use the `gh` CLI for all operations.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Add or remove labels with `gh issue edit`.
- Close: `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill operations

When a skill says “publish to the issue tracker”, create a GitHub issue.

When a skill says “fetch the relevant ticket”, run:

`gh issue view <number> --comments`

GitHub issues are also used by `wayfinder` for maps, child tickets,
dependencies, claims and resolutions. Prefer native GitHub sub-issues and
issue dependencies when available; otherwise use task lists and explicit
`Blocked by: #<number>` references.

# {{name}}

A theflow hub: one Claude session per piece of work, dispatched from here. The shared rules:

@{{flow}}/HUB.md

The sections below are this hub's own and win over the shared rules. Delete the ones you don't need.

## Bases

One line per base: what it is, its default branch, anything a session should know before touching it.

- `api`: the backend (default branch `main`).

## Tickets

Leave this section out if the hub has no tracker. Otherwise say:

- what a ticket reference looks like (an ID pattern like `ABC-123`, a URL) and how to read one (an MCP integration,
  a CLI),
- whether every code item needs a ticket (then `work-on` looks for one or drafts one for approval), and where new
  ones go,
- the note field that holds its link (default `ticket:`).

## Kickoff extras

Anything every kickoff must add (a team convention, a verification command, a writing style for PR descriptions).

## Wrap-up extras

Where open follow-ups go (a task list, a ticket), and anything else to do before an item closes.

# `.claude/` reference

What's in this directory, why, and the syntax for each piece — so you can
extend it without re-deriving the format. Everything here already works;
this is annotation, not aspiration.

## Directory map

```
CLAUDE.md                    # repo root — persistent project memory, read every session
.mcp.json                    # repo root — MCP server connections for this project
.claude/
├── settings.json            # team-shared config: permissions, env, hooks (committed)
├── settings.local.json      # personal overrides (gitignored — see .gitignore)
├── commands/*.md            # custom slash commands
├── agents/*.md               # custom subagents
└── skills/<name>/SKILL.md   # custom skills (can bundle reference files/scripts)
```

## `CLAUDE.md`

Plain markdown, no frontmatter. Loaded into context automatically every
session — this is the one file Claude always has, so it should hold the
things you'd otherwise repeat every conversation (stack, hard rules,
conventions), not things derivable by reading the code. Root-level
`CLAUDE.md` applies to the whole repo; a `CLAUDE.md` inside a subdirectory
(e.g. `backend/CLAUDE.md`) would apply only when working in that subtree.

## `settings.json` — permissions, env, hooks

Full schema is large; here's the syntax for what's actually in this repo's
`settings.json`.

**Permissions** — gate tool calls without a prompt each time:

```json
"permissions": {
  "allow": ["Bash(uv run pytest*)"],   // trailing * = prefix match
  "deny":  ["Read(~/.rentcomp/secrets.json)"],
  "ask":   ["Edit(//etc/*)"]           // always confirm, even if allow would match
}
```

Rule syntax is `Tool` or `Tool(pattern)`. `"Bash(git *)"` matches `git`,
`git status`, `git commit -m ...`. `"Read"` alone (no parens) allows the tool
unconditionally. `deny` always wins over `allow`.

**Env** — variables set for every Claude Code session in this repo:

```json
"env": { "RENTCOMP_LIVE": "0" }
```

**Hooks** — shell commands run at lifecycle events. Structure:

```json
"hooks": {
  "<EventName>": [
    {
      "matcher": "Write|Edit",        // regex over tool name; omit to match all
      "hooks": [
        { "type": "command", "command": "...", "timeout": 30 }
      ]
    }
  ]
}
```

Common events: `PreToolUse` (can block the call), `PostToolUse` (after it
succeeds), `Stop`, `SessionStart`, `UserPromptSubmit`, `PreCompact`. The
command receives JSON on stdin (`tool_name`, `tool_input`, and on
`PostToolUse` also `tool_response`) and can return JSON to talk back —
`{"systemMessage": "..."}` shows a note to the user, `{"continue": false}`
blocks. This repo's hook reads `tool_input.file_path`, checks whether it's
under `stats/`/`pipeline/`, and if so prints a `systemMessage` nudging you to
run `/test`. Easiest way to see it fire: once `backend/src/rentcomp/stats/`
exists, edit a file in it and watch for the reminder after the edit. To test
the matching logic itself without waiting for that directory to exist:

```bash
python3 -c "
import re
p = 'backend/src/rentcomp/stats/weighted.py'
print('fires' if re.search(r'/(stats|pipeline)/.*\.py\$', p) else 'no match')
"
```

`settings.json` is team-shared and committed. `settings.local.json` uses the
identical schema but is for personal overrides and is gitignored — Claude
Code merges user → project (`settings.json`) → local
(`settings.local.json`), with later sources winning.

## `commands/*.md` — custom slash commands

One file = one command, invoked as `/<filename-without-.md>`. Frontmatter +
a prompt body:

```markdown
---
description: One-line summary shown in the command picker
argument-hint: <method> <path>        # shown as a hint while typing
allowed-tools: Bash(uv run pytest*)   # optional — restricts tools for this command
model: sonnet                          # optional — override model for this command
---

Prompt body. Use $ARGUMENTS for everything typed after the command name,
or $1 / $2 for positional args individually.
```

This repo has two:
- `/test` — no arguments, runs the full pytest/Vitest/Playwright gate
- `/new-endpoint POST /api/decisions` — takes `$ARGUMENTS`, scaffolds a route

## `agents/*.md` — custom subagents

One file = one subagent, invoked via the `Agent` tool or by name. Frontmatter
+ a system-prompt body:

```markdown
---
name: backend-reviewer
description: When to invoke this agent — the orchestrating Claude reads this to decide whether it's relevant, so be specific about triggers.
tools: Read, Grep, Glob, Bash          # optional — omit to inherit all tools
model: sonnet                          # optional — override model for this agent
---

System prompt for the subagent. Runs as a fresh context with no memory of
the parent conversation — everything it needs must be stated here or passed
in the invocation prompt.
```

This repo has two, both read-only by design (`tools:` excludes `Edit`/`Write`
so they can report findings but not "fix" what they're reviewing):
- `backend-reviewer` — checks Python changes against the architecture's hard
  rules (layering, kNN leakage, dependency budget, cache durability)
- `frontend-reviewer` — checks the frontend stays a pure view layer

## `skills/<name>/SKILL.md` — custom skills

Same underlying invocation mechanism as commands — in fact `/test` and
`/new-endpoint` from `commands/` show up in the `Skill` tool's listing too —
but skills support a larger frontmatter surface and can bundle files
alongside the prompt. Directory name is the invocation name
(`skills/add-pipeline-stage/` → `/add-pipeline-stage`); a `name:` field only
sets the *display* label, it doesn't rename the command.

```markdown
---
name: add-pipeline-stage
description: What it does + when to use it — this is what Claude reads to decide whether to auto-invoke, so be specific about triggers and non-triggers.
argument-hint: <stage-name> <insert-after-stage>
allowed-tools: Read, Write, Edit, Glob, Grep   # pre-approved for this skill's turn
disallowed-tools: Bash                          # optional — block tools while active
disable-model-invocation: false                 # true = manual-only, /name required
user-invocable: true                            # false = Claude-only, hidden from `/` menu
paths: ["backend/src/rentcomp/pipeline/**"]     # optional — only relevant when editing matching files
model: sonnet                                   # optional — override model for this skill
effort: high                                    # optional — low/medium/high/xhigh/max
context: fork                                   # optional — run in an isolated subagent
agent: Explore                                  # subagent type when context: fork
---

Prompt body. $ARGUMENTS for everything after the name, or named args via
an `arguments:` frontmatter field for $stage_name-style substitution.
Reference bundled files with a relative link: [reference.md](reference.md).
```

Bundling a reference file keeps `SKILL.md` itself short — the skill points
to it rather than inlining everything:

```
.claude/skills/add-pipeline-stage/
├── SKILL.md        # entry point; frontmatter + short instructions
└── reference.md     # detail SKILL.md links to instead of inlining
```

This repo has one real skill, not just a demo: `add-pipeline-stage`
scaffolds a new stage in the comp pipeline (module + wiring + tests) and
links out to `reference.md` for the current stage order and layering
contract. It's a skill rather than a command because it's multi-step and
benefits from the bundled reference doc — a single command prompt would
either be too long or leave that detail out.

## `.mcp.json` — MCP server connections

Project-root file (sibling to `CLAUDE.md`, not inside `.claude/`) declaring
external tool servers Claude can connect to:

```json
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"]
    },
    "example-remote-server": {
      "type": "http",
      "url": "https://example.com/mcp"
    }
  }
}
```

`type: "stdio"` servers are a local process Claude Code launches
(`command` + `args`, optionally `env`); `type: "http"` servers are a remote
endpoint reached over the network. The `playwright` entry here is real and
useful, not a placeholder — this project already committed to Playwright
for E2E (D22), so this MCP server lets Claude drive a real browser directly
during debugging instead of only reading test output. It needs Node/npx on
PATH; nothing installs until the server is actually approved and launched.
`example-remote-server` is a literal placeholder — swap the URL for a real
one or delete the block.

**Trust flow:** a fresh `.mcp.json` doesn't connect silently. The first time
a session in this repo starts, Claude Code prompts to approve each server
(or approve later via `/mcp`) — this is what stops a cloned repo from
launching arbitrary processes without consent. `settings.json` has
`enableAllProjectMcpServers` / `enabledMcpjsonServers` /
`disabledMcpjsonServers` fields to pre-approve or block specific servers
once you've decided you trust them, but this repo doesn't set them yet —
the servers above will prompt for approval on first use.

*Two things I couldn't fully confirm and didn't want to guess into a
template:* the exact env-var interpolation syntax inside `.mcp.json`
`env` values (docs reference it existing but don't spell out `${VAR}` vs.
something else), and whether `http`-type servers take an `env` block the
same way `stdio` ones do, or need a separate `headers` field for auth
instead. If you add an authenticated remote server, check `/mcp` or
`claude mcp add --help` in a live session rather than trusting this file's
silence on the topic.

## Not yet scaffolded

- **A formatter/lint hook** (`PostToolUse` on `Write|Edit` running
  `ruff format` / `prettier`) — deferred until `backend/`/`frontend/` exist
  and a formatter is actually installed; a hook that shells out to a missing
  binary fails silently in a confusing way.

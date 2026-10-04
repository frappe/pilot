# Rejected patterns

Patterns agents produced and the team rejected. Do not repeat them.

## Output

| Pattern | Team's words |
|---|---|
| Long summaries, unasked tables | "no verbosity" |
| Explaining why a change was made | "i dont need to know the reason of change, if needed will ask you" |
| Re-proposing a settled spec | "whatever i said do it man" |
| Repeating that tests are missing | "dont tell everytime" |
| Confident wrong claim, then a "fix" | "please dont do any shit changes" |
| Rabbit holes, wrong layer | "stop it, waste of time", "wait you going in wrong direction" |

## Code

| Pattern | Team's words |
|---|---|
| Comments restating code or the change | "remove useless comments", "i have trimmed comments, dont add back" |
| File-top comments | "please remove comment from top of the file" |
| Comments hinting at old code | "dont add comment which gives old implementation vibe" |
| Magic constants for keys and labels | "dont make everything magic constant" |
| Status mapping tables | "lets not use mapping, use the same state" |
| Many tiny private helpers, `helpers.py` | "dont keep adding lot of private functions", "keep those inline instead" |
| Clever save helpers | "simple code is much better than clever code" |
| `save_progress()` steps | "the functions can have save: bool = True" |
| `cast()`, relative imports, `#:` | "import the class in type checking block", "use absolute", "use normal python comment" |
| Abbreviations | "engg should understand what the method does from method name" |
| Defensive code to pass a test | "fix the test then?" |
| Trimming real rationale | "you removed lot of context on why multi attempt needed" |
| Logic in CLI commands or routes | Put it on `Bench`, `Site`, `App`, a manager or a task. |
| Bench and site passed to a helper object | Use `bench.site(...)` methods. |

## Design

| Pattern | Team's words |
|---|---|
| Unneeded knobs | "lets not adding this flexibility to modify path", "remove the pilot.branch option" |
| Hardcoded consumer special cases | "i dont like this hardcoding" |
| Extra infrastructure for convenience | "why we need the hub, its NxN na?" |
| Internal feature as a status | "just stopped status is good" |
| Partial pending states | "tell user that out of capacity and retry later" |
| Cache invalidated every second | "its useless. revert it" |
| Binding to `0.0.0.0` | "a big no" |
| Storing derivable values or private keys in records | "that's a promise", "assume that is on disk" |

## Docs and UI

| Pattern | Team's words |
|---|---|
| Hard-wrapped Markdown | "keep continuous line" |
| Docs for obvious behavior | "natural expectation" |
| Over-trimmed or rewritten copy | "you made the docs worse", "old one was better. you missing the point" |
| Old-implementation docs, duplication | "current state only", "i hate duplication anywhere" |
| Bad diagrams | "very bad quality diagram man" |
| Field descriptions everywhere, CSS hacks | "keep minimal nice", "just give some title for section" |
| Colored cards, useless polish | "please no color in card", "revert it, useless" |

## Workflow

| Pattern | Team's words |
|---|---|
| Unapproved commit or push | "before commit ask me from next time", "dont push man" |
| `git stash` on the team's tree | "hae you stashed my changes??" |
| Dev servers or browser automation in the frontend without asking | "dont do it" |
| Tests on a site with real data | "never run unit test on atlas.localhost" |
| Committed plan files, odd messages | "dont use the word clarify" |
| Long PR bodies, literal `\n` | "keep it smaller and compact man" |
| Changing another repo's flow | "fix it in atlas's install-cargo.sh" |

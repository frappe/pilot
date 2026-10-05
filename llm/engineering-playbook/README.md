# Engineering playbook

Shared agent skill for the Pilot team: workflow, environment access, code taste, review, design defaults and rejected patterns. `SKILL.md` is the entry point and lists every reference. `settings/` holds the auto mode classifier rules and a merge script.

## Install

Link the skill from the repository root:

```sh
ln -s "$PWD/llm/engineering-playbook" ~/.claude/skills/pilot-engineering-playbook
ln -s "$PWD/llm/engineering-playbook" ~/.agents/skills/pilot-engineering-playbook
```

Merge the shared classifier rules into your Git-ignored `.claude/settings.local.json`. The merge keeps your settings and unions the `autoMode` lists:

```sh
mkdir -p .claude
[ -f .claude/settings.local.json ] || echo '{}' > .claude/settings.local.json
cp .claude/settings.local.json .claude/settings.local.json.bak
jq -s -f llm/engineering-playbook/settings/merge.jq .claude/settings.local.json llm/engineering-playbook/settings/auto-mode.json > .claude/settings.merged.json
mv .claude/settings.merged.json .claude/settings.local.json
jq '.autoMode.environment' .claude/settings.local.json
```

Check that your entries remain, delete the backup, then declare your targets. See [environments.md](references/environments.md#declare-your-environments).

## Change the playbook

Edit the reference that owns the rule. Keep `SKILL.md` short. Update `CLAUDE.md` and `.greptile/rules.md` in the same pull request when a rule changes.

# Shared custom agent definitions

This directory is the single source of truth for LinuxStreamDeck's custom
agents. It prevents the Claude and Codex copies from drifting while preserving
each provider's native discovery format.

- `manifest.json` contains names, trigger descriptions and provider-specific
  model, tool and sandbox settings.
- `prompts/*.md` contains the shared system prompt for each agent.
- `sync.py` generates `.claude/agents/*.md` and `.codex/agents/*.toml`.

Never edit a generated agent file directly. Change the manifest or canonical
prompt, regenerate both provider adapters, and verify them:

```bash
python3 agent-definitions/sync.py
python3 agent-definitions/sync.py --check
```

Both commands use only the Python standard library. Keep `CUSTOMAGENTS.md` in
sync with the manifest whenever an agent is added, removed or its public
purpose changes.

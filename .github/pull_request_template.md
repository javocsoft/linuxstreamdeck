<!-- Thanks for contributing to LinuxStreamDeck! Please fill in the sections below. -->

## Summary

<!-- What does this PR change, and why? -->

## Related issue

<!-- e.g. Closes #123. Leave blank if none. -->

## Type of change

- [ ] Bug fix
- [ ] New feature (e.g. a new OBS or system action)
- [ ] Documentation only
- [ ] Refactor / internal change

## How was it tested?

<!-- Describe how you verified the change. -->

```bash
# Compile check
.venv/bin/python -m compileall -q linuxstreamdeck

# Test suite (with an isolated config directory)
TEST_CONFIG_DIR="$(mktemp -d)"
LSD_CONFIG_DIR="$TEST_CONFIG_DIR" .venv/bin/python -m unittest discover -s tests -v
```

## Checklist

- [ ] The compile check passes.
- [ ] The test suite passes with an isolated `LSD_CONFIG_DIR` (never the real config).
- [ ] All new user-facing text and comments are in English, with no accented characters.
- [ ] I matched the style and conventions of the files I edited.
- [ ] Documentation (`README.md`, `AGENTS.md`, etc.) is updated if behaviour, commands or structure changed.
- [ ] If I changed agent prompts under `agent-definitions/`, I ran `python3 agent-definitions/sync.py` and `--check`.

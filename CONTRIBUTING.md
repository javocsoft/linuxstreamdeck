# Contributing to LinuxStreamDeck

Thank you for helping improve LinuxStreamDeck. Contributions of code,
documentation, testing, bug reports, and practical feedback are welcome.

By participating in this project, you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Choose the right place

- Use **GitHub Issues** for reproducible bugs and concrete, actionable work.
- Use **GitHub Discussions** for setup help, hardware compatibility questions,
  open-ended ideas, and examples of your Stream Deck setup.
- Report vulnerabilities privately by following [SECURITY.md](SECURITY.md).
  Do not disclose security issues in an Issue or Discussion.

Before opening a new report, search existing Issues and Discussions. Include
the Linux distribution, desktop environment, Stream Deck model, application
version, OBS version when relevant, reproduction steps, expected behavior, and
actual behavior. Remove credentials and personal information from logs.

## Read the project guide

[AGENTS.md](AGENTS.md) is the technical and operational source of truth for the
repository. Read it before changing code. It explains the architecture, build
commands, threading rules, rendering constraints, configuration isolation, and
documentation workflow.

Important project rules include:

- Keep all code, comments, logs, documentation, and user-facing text in English.
- Route communication through the thread-safe EventBus. UI code must not talk
  directly to the physical deck or OBS.
- Keep Pillow rendering behind `RENDER_LOCK`, and retain the BASIC Pillow text
  layout engine described in `AGENTS.md`.
- Serialize each complete OBS websocket request under the client lock.
- Never let a test or experiment write to the user's real configuration.
- Edit canonical agent prompts only under `agent-definitions/`; the Claude and
  Codex adapters are generated.

## On AI-assisted development

Parts of this project were written with AI assistance. That is visible in the
repository rather than hidden: `AGENTS.md` and `CLAUDE.md` are the operational
guides those tools follow, and they are the same guides a human contributor
should read.

This is not "vibe coding" in the sense of accepting generated code without
reading it. The architecture and the design decisions are deliberate, and the
project is held to them by its test suite — in particular
`tests/test_invariants.py`, which tests no feature at all and exists solely to
make the threading and rendering rules of `AGENTS.md` fail loudly instead of
silently. Each of its guards is mutation-tested: confirmed to fail when the rule
it protects is deliberately broken.

**Contributions are judged on the contribution, not on how you produced it.**
Use AI, don't use AI — nobody will ask, and there is no way to tell. What is
asked is what it has always been: the tests pass, the rules above are respected,
the change is focused, and the documentation is updated when behaviour changes.

## Set up the development environment

From the repository root:

```bash
./build.sh
```

This checks the generated agent adapters, prepares `.venv` with access to the
system PyGObject installation, installs Python dependencies, and performs a
compile check.

To install missing apt dependencies as part of setup:

```bash
./build.sh --apt
```

The `--apt` option uses `sudo`. Review the package list printed by the script
before approving it.

## Make a focused change

1. Fork the repository and create a branch from `main`.
2. Keep the change focused on one bug, feature, or documentation improvement.
3. Follow the style and naming of the surrounding code.
4. Add or update tests for behavior that can be exercised without hardware.
5. Update user and agent documentation when commands, behavior, architecture,
   dependencies, or conventions change.
6. Do not commit secrets, personal configuration, generated packages, virtual
   environments, caches, or editor files.

For a new OBS or system action, use the declarative `Action` and `Param`
framework. Keep blocking device work outside the GTK main thread, route every
OBS request through the serialized `OBSClient`, and marshal UI updates through
the EventBus.

## Verify the change

Always isolate tests and scripts that may save configuration. Set
`LSD_CONFIG_DIR` before Python imports the project:

```bash
TEST_CONFIG_DIR="$(mktemp -d)"
LSD_CONFIG_DIR="$TEST_CONFIG_DIR" \
  .venv/bin/python -m unittest discover -s tests -v
```

Then run the remaining checks:

```bash
.venv/bin/python -m compileall -q linuxstreamdeck
python3 agent-definitions/sync.py --check
git diff --check
```

Do not use a background GUI launch as verification. It can leave stale
single-instance processes and cached rendering state. Test rendering offscreen
as described in `AGENTS.md`. In the pull request, explain any physical hardware
or OBS behavior that still requires maintainer validation.

If you change canonical agent prompts or their manifest, run:

```bash
python3 agent-definitions/sync.py
python3 agent-definitions/sync.py --check
```

Commit both the canonical changes and regenerated adapters.

## Submit a pull request

A pull request should include:

- A clear summary of the problem and the chosen solution.
- Relevant Issue or Discussion links.
- The checks that were run and their results.
- Screenshots or offscreen renders for visible UI or key-rendering changes.
- Documentation updates required by the change.
- Known limitations or follow-up work.

Keep review discussion technical and respectful. Be prepared to revise the
change when maintainers identify concurrency, rendering, configuration, or
compatibility risks.

## License

By submitting a contribution, you agree that it is licensed under the
[GNU General Public License version 3 or later](LICENSE), the same license as
LinuxStreamDeck.

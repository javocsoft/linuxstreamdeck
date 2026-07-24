"""Stable AI provider identifiers and default model choices."""

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDERS = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC)

# These are user-editable defaults. Keeping one model per provider in the
# configuration lets users switch models without requiring an application update.
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"

PROVIDER_LABELS = {
    PROVIDER_OPENAI: "OpenAI",
    PROVIDER_ANTHROPIC: "Claude",
}


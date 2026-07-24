from __future__ import annotations

import unittest

from linuxstreamdeck.core.secrets import ApiKeyStore


class FakeSecretBackend:
    COLLECTION_DEFAULT = "default"

    def __init__(self) -> None:
        self.saved_key = ""
        self.calls: list[tuple] = []

    def password_lookup(
        self, schema, attributes, cancellable, callback, user_data
    ) -> None:
        self.calls.append(("lookup", attributes))
        callback(self, "lookup-result", user_data)

    def password_lookup_finish(self, result) -> str:
        return self.saved_key

    def password_store(
        self,
        schema,
        attributes,
        collection,
        label,
        password,
        cancellable,
        callback,
        user_data,
    ) -> None:
        self.calls.append(("store", attributes, label, password))
        self.saved_key = password
        callback(self, "store-result", user_data)

    def password_store_finish(self, result) -> bool:
        return True

    def password_clear(
        self, schema, attributes, cancellable, callback, user_data
    ) -> None:
        self.calls.append(("clear", attributes))
        self.saved_key = ""
        callback(self, "clear-result", user_data)

    def password_clear_finish(self, result) -> bool:
        return True


class ApiKeyStoreTests(unittest.TestCase):
    def test_keys_are_scoped_by_provider(self) -> None:
        backend = FakeSecretBackend()
        store = ApiKeyStore(backend=backend)
        stored = []
        loaded = []

        store.store(
            "openai",
            "openai-secret",
            lambda success, error: stored.append((success, error)),
        )
        store.lookup(
            "openai",
            lambda key, error: loaded.append((key, error)),
        )

        self.assertEqual(stored, [(True, None)])
        self.assertEqual(loaded, [("openai-secret", None)])
        self.assertEqual(backend.calls[0][1], {"provider": "openai"})
        self.assertEqual(backend.calls[1][1], {"provider": "openai"})

    def test_empty_key_removes_saved_credential(self) -> None:
        backend = FakeSecretBackend()
        backend.saved_key = "claude-secret"
        results = []

        ApiKeyStore(backend=backend).store(
            "anthropic",
            "",
            lambda success, error: results.append((success, error)),
        )

        self.assertEqual(results, [(True, None)])
        self.assertEqual(backend.saved_key, "")
        self.assertEqual(backend.calls[0], ("clear", {"provider": "anthropic"}))

    def test_unknown_provider_is_rejected(self) -> None:
        results = []
        ApiKeyStore(backend=FakeSecretBackend()).lookup(
            "unknown",
            lambda key, error: results.append((key, error)),
        )

        self.assertEqual(results[0][0], "")
        self.assertIsInstance(results[0][1], ValueError)


if __name__ == "__main__":
    unittest.main()

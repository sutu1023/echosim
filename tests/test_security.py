"""密码哈希与输入校验的单元测试。"""

from __future__ import annotations

import unittest

from app.security import (
    hash_password,
    normalize_email,
    validate_email,
    validate_new_password,
    verify_password,
)


class PasswordHashTests(unittest.TestCase):
    def test_hash_is_salted_and_verifiable(self) -> None:
        first = hash_password("correct horse battery")
        second = hash_password("correct horse battery")

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("scrypt$16384$8$1$"))
        self.assertTrue(verify_password("correct horse battery", first))
        self.assertTrue(verify_password("correct horse battery", second))

    def test_wrong_password_is_rejected(self) -> None:
        encoded = hash_password("correct horse battery")

        self.assertFalse(verify_password("wrong password", encoded))

    def test_tampered_hash_is_rejected(self) -> None:
        encoded = hash_password("correct horse battery")
        parts = encoded.split("$")
        parts[1] = "999"
        tampered = "$".join(parts)

        self.assertFalse(verify_password("correct horse battery", tampered))
        self.assertFalse(verify_password("correct horse battery", "not-a-hash"))


class ValidationTests(unittest.TestCase):
    def test_email_is_normalized(self) -> None:
        self.assertEqual(normalize_email("  A@Example.COM "), "a@example.com")
        self.assertIsNone(validate_email("  A@Example.COM "))

    def test_invalid_email_is_rejected(self) -> None:
        self.assertIsNotNone(validate_email("not-an-email"))
        self.assertIsNotNone(validate_email(""))
        self.assertIsNotNone(validate_email("a@" + "x" * 260))

    def test_password_strength(self) -> None:
        self.assertIsNone(validate_new_password("a" * 12))
        self.assertIsNotNone(validate_new_password("short"))
        self.assertIsNotNone(validate_new_password("a" * 129))


if __name__ == "__main__":
    unittest.main()

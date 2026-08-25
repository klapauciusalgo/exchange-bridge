"""Tests for SecurityManager, encryption, PIN verification, and log redaction."""
import logging
import pytest
from config.security import SecurityManager, RedactingFilter


def test_encryption_and_decryption(master_key: str):
    sec = SecurityManager(master_key=master_key)
    secret_text = "mx0val_super_secret_mexc_api_key_12345"

    encrypted = sec.encrypt(secret_text)
    assert encrypted.startswith("enc:")
    assert encrypted != secret_text

    decrypted = sec.decrypt(encrypted)
    assert decrypted == secret_text


def test_encryption_without_key():
    sec = SecurityManager(master_key=None)
    plain = "plain_secret"
    assert sec.encrypt(plain) == plain
    assert sec.decrypt(plain) == plain


def test_pin_hashing_and_verification():
    pin = "1234"
    salt, pin_hash = SecurityManager.hash_pin(pin)
    assert salt is not None
    assert pin_hash is not None

    # Correct PIN
    assert SecurityManager.verify_pin("1234", salt, pin_hash) is True
    # Incorrect PIN
    assert SecurityManager.verify_pin("9999", salt, pin_hash) is False
    assert SecurityManager.verify_pin("12345", salt, pin_hash) is False


def test_session_lifecycle():
    sec = SecurityManager()
    user_id = 111222333

    assert sec.is_session_active(user_id) is False
    sec.refresh_session(user_id)
    assert sec.is_session_active(user_id, timeout_minutes=30) is True

    # Test timeout check
    assert sec.is_session_active(user_id, timeout_minutes=-1) is False
    assert sec.is_session_active(user_id) is False

    # Revoke
    sec.refresh_session(user_id)
    assert sec.is_session_active(user_id) is True
    sec.revoke_session(user_id)
    assert sec.is_session_active(user_id) is False


def test_redacting_log_filter():
    api_key = "mx0val_sensitive_key"
    secret_key = "sensitive_secret_999"
    bot_token = "bot123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ1234567"

    redactor = RedactingFilter(patterns_to_redact=[api_key, secret_key])

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg=f"Calling API with {api_key} and token={bot_token}",
        args=(),
        exc_info=None
    )
    redactor.filter(record)
    assert api_key not in record.msg
    assert "[REDACTED]" in record.msg

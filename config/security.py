"""Security utilities for credential encryption, PIN verification, and log redaction."""
import base64
import hashlib
import hmac
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecurityManager:
    """Handles encryption/decryption of exchange secrets and PIN session authorization."""

    def __init__(self, master_key: Optional[str] = None):
        self._fernet: Optional[Fernet] = None
        if master_key:
            try:
                # If key is already 32-byte urlsafe base64
                if len(master_key) == 44 and master_key.endswith("="):
                    self._fernet = Fernet(master_key.encode("utf-8"))
                else:
                    # Derive a 32-byte key using PBKDF2
                    salt = b"mexc_tg_bridge_salt_v1"
                    kdf = PBKDF2HMAC(
                        algorithm=hashes.SHA256(),
                        length=32,
                        salt=salt,
                        iterations=100_000,
                    )
                    derived_key = base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8")))
                    self._fernet = Fernet(derived_key)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to initialize Fernet encryption with master key: {e}")

        self._active_sessions: Dict[int, float] = {}  # user_id -> last_activity_timestamp

    @staticmethod
    def generate_master_key() -> str:
        """Generate a new Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    def encrypt(self, plain_text: str) -> str:
        """Encrypt plain text string using Fernet."""
        if not plain_text:
            return ""
        if self._fernet is None:
            # If no encryption key is set, return as is (with warning in real environment)
            return plain_text
        encrypted_bytes = self._fernet.encrypt(plain_text.encode("utf-8"))
        return "enc:" + encrypted_bytes.decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        """Decrypt cipher text string."""
        if not cipher_text:
            return ""
        if not cipher_text.startswith("enc:"):
            return cipher_text
        if self._fernet is None:
            raise ValueError("Cannot decrypt: MASTER_ENCRYPTION_KEY is not configured.")
        raw_token = cipher_text[4:].encode("utf-8")
        try:
            decrypted_bytes = self._fernet.decrypt(raw_token)
            return decrypted_bytes.decode("utf-8")
        except InvalidToken:
            raise ValueError("Failed to decrypt secret: Invalid master key or corrupted token.")

    @staticmethod
    def hash_pin(pin: str, salt: Optional[str] = None) -> Tuple[str, str]:
        """Hash a PIN with salt. Returns (salt, hex_digest)."""
        if salt is None:
            salt = os.urandom(16).hex()
        digest = hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()
        return salt, digest

    @staticmethod
    def verify_pin(pin: str, salt: str, expected_hash: str) -> bool:
        """Verify PIN against salt and expected hash using constant-time comparison."""
        actual_hash = hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual_hash, expected_hash)

    def is_session_active(self, user_id: int, timeout_minutes: int = 30) -> bool:
        """Check if user has an active, non-expired PIN session."""
        last_active = self._active_sessions.get(user_id)
        if last_active is None:
            return False
        if time.time() - last_active > (timeout_minutes * 60):
            self._active_sessions.pop(user_id, None)
            return False
        return True

    def refresh_session(self, user_id: int) -> None:
        """Record or refresh session activity for user."""
        self._active_sessions[user_id] = time.time()

    def revoke_session(self, user_id: int) -> None:
        """Revoke user's active session."""
        self._active_sessions.pop(user_id, None)


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs API keys, secrets, tokens, and PINs from logs."""

    def __init__(self, patterns_to_redact: Optional[List[str]] = None):
        super().__init__()
        self.patterns = [p for p in (patterns_to_redact or []) if p and len(p) >= 4]
        self.sensitive_key_val_regex = re.compile(
            r"((?:ApiKey|apiKey|api_key|secret|secret_key|token|signature|Signature)[:=]\s*['\"]?)([a-zA-Z0-9_\-\.\:]{8,})(['\"]?)",
            re.IGNORECASE
        )
        self.bot_token_regex = re.compile(r"bot\d+:[A-Za-z0-9_-]{35}")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in self.patterns:
            msg = msg.replace(p, "[REDACTED]")

        msg = self.sensitive_key_val_regex.sub(r"\1[REDACTED]\3", msg)
        msg = self.bot_token_regex.sub("bot[REDACTED_TOKEN]", msg)

        record.msg = msg
        record.args = ()
        return True

"""Tests for Kalshi RSA-PSS authentication."""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from niche_scanner.kalshi.auth import KalshiAuth


def _generate_test_key(tmp_path: Path) -> Path:
    """Generate an RSA key pair and write the private key to tmp_path."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "test_key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    return key_path


def test_sign_produces_valid_signature(tmp_path: Path) -> None:
    """Sign a request and verify the signature with the public key."""
    key_path = _generate_test_key(tmp_path)
    auth = KalshiAuth(api_key_id="test-key-id", private_key_path=str(key_path))

    timestamp_ms = 1700000000000
    method = "GET"
    path = "/trade-api/v2/markets"

    headers = auth.sign(timestamp_ms, method, path)
    signature_bytes = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = f"{timestamp_ms}{method}{path}".encode()

    # Load public key from the same PEM file
    private_key = serialization.load_pem_private_key(
        key_path.read_bytes(), password=None,
    )
    public_key = private_key.public_key()

    # verify() raises InvalidSignature on failure; no exception means success
    public_key.verify(
        signature_bytes,
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_sign_different_timestamps_produce_different_sigs(tmp_path: Path) -> None:
    """Same key, different timestamps must produce different signatures."""
    key_path = _generate_test_key(tmp_path)
    auth = KalshiAuth(api_key_id="key-1", private_key_path=str(key_path))

    h1 = auth.sign(1000, "GET", "/trade-api/v2/markets")
    h2 = auth.sign(2000, "GET", "/trade-api/v2/markets")

    assert h1["KALSHI-ACCESS-SIGNATURE"] != h2["KALSHI-ACCESS-SIGNATURE"]
    assert h1["KALSHI-ACCESS-TIMESTAMP"] == "1000"
    assert h2["KALSHI-ACCESS-TIMESTAMP"] == "2000"


def test_auth_loads_pem_file(tmp_path: Path) -> None:
    """Verify the private key loads successfully from a PEM file."""
    key_path = _generate_test_key(tmp_path)
    auth = KalshiAuth(api_key_id="my-key", private_key_path=str(key_path))

    assert auth.api_key_id == "my-key"
    # _private_key should be an RSA private key instance
    assert auth._private_key is not None
    assert hasattr(auth._private_key, "sign")

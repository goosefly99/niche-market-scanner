"""RSA-PSS authentication for Kalshi API."""

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiAuth:
    """Signs Kalshi API requests using RSA-PSS (SHA-256)."""

    _private_key: rsa.RSAPrivateKey

    def __init__(self, api_key_id: str, private_key_path: str) -> None:
        self.api_key_id = api_key_id
        with open(private_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            msg = "Kalshi auth requires an RSA private key"
            raise TypeError(msg)
        self._private_key = key

    def sign(self, timestamp_ms: int, method: str, path: str) -> dict[str, str]:
        """Sign a Kalshi API request. Returns the 3 required headers."""
        message = f"{timestamp_ms}{method}{path}".encode()
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": signature.hex(),
        }

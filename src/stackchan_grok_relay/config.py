from dataclasses import dataclass
import ipaddress
import math
import os
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    webhook_url: str
    webhook_sender_key: str
    timeout_seconds: float
    max_reply_length: int

    @classmethod
    def from_env(cls) -> "Config":
        host = os.getenv("RELAY_HOST", "127.0.0.1")
        _validate_private_host(host)

        port = _read_int("RELAY_PORT", 8787, 1, 65535)
        webhook_url = os.getenv("GROK_WEBHOOK_URL", "")
        parsed_url = urlparse(webhook_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ConfigError("GROK_WEBHOOK_URL に有効な HTTP URL を指定してください。")

        webhook_sender_key = os.getenv("GROK_WEBHOOK_SENDER_KEY", "")
        if not webhook_sender_key:
            raise ConfigError("GROK_WEBHOOK_SENDER_KEY を指定してください。")

        return cls(
            host=host,
            port=port,
            webhook_url=webhook_url,
            webhook_sender_key=webhook_sender_key,
            timeout_seconds=_read_float("GROK_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
            max_reply_length=_read_int("MAX_REPLY_LENGTH", 120, 1, 500),
        )


def _validate_private_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ConfigError("RELAY_HOST にはプライベート IPv4 アドレスを指定してください。") from error
    if address.version != 4 or not (
        address.is_loopback or any(address in network for network in PRIVATE_NETWORKS)
    ):
        raise ConfigError("RELAY_HOST に公開アドレスは指定できません。")


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} には整数を指定してください。") from error
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} は {minimum} 以上 {maximum} 以下にしてください。")
    return value


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} には数値を指定してください。") from error
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} は {minimum} 以上 {maximum} 以下にしてください。")
    return value

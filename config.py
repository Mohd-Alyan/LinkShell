"""
config.py — Configuration and identity management.

Stores the user's UUID, keys, and username in ``~/.linkshell/config.json``.
Also maintains a local persistent history of seen peers in
``~/.linkshell/peers.json``.
"""

import json
import os
import uuid as _uuid
from pathlib import Path
from dataclasses import dataclass, asdict

# ── Paths ──────────────────────────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / ".linkshell"
CONFIG_FILE = CONFIG_DIR / "config.json"
PEERS_FILE  = CONFIG_DIR / "peers.json"
RECEIVED_DIR = Path.home() / "Downloads" / "linkshell"

DEFAULT_UDP_PORT = 9876
DEFAULT_TCP_PORT = 9877


# ── Data class ─────────────────────────────────────────────────────────────────

@dataclass
class UserConfig:
    uuid: str
    username: str
    private_key: str = ""
    public_key: str = ""
    tcp_port: int = DEFAULT_TCP_PORT
    udp_port: int = DEFAULT_UDP_PORT


# ── Load / Save ────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RECEIVED_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.is_file()


def load_config() -> UserConfig:
    """Load existing config from disk.  Raises FileNotFoundError if missing."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return UserConfig(**data)


def save_config(cfg: UserConfig) -> None:
    _ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, indent=2)


def register(username: str, tcp_port: int = DEFAULT_TCP_PORT,
             udp_port: int = DEFAULT_UDP_PORT) -> UserConfig:
    """Create a new identity and persist it."""
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives import serialization

    # Generate X25519 Keypair
    priv_key = x25519.X25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    
    priv_bytes = priv_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    pub_bytes = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )

    cfg = UserConfig(
        uuid=str(_uuid.uuid4()),
        username=username.strip(),
        private_key=priv_bytes.hex(),
        public_key=pub_bytes.hex(),
        tcp_port=tcp_port,
        udp_port=udp_port,
    )
    save_config(cfg)
    return cfg


# ── Peer registry ─────────────────────────────────────────────────────────────

def load_peers() -> dict:
    """Return {uuid: {username, ip, ...}} from disk, or empty dict."""
    if not PEERS_FILE.is_file():
        return {}
    with open(PEERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_peers(peers: dict) -> None:
    _ensure_dirs()
    with open(PEERS_FILE, "w", encoding="utf-8") as f:
        json.dump(peers, f, indent=2)

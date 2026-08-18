"""
discovery.py — UDP broadcast-based peer discovery for LAN Chat.

Two daemon threads run continuously:
  • **Broadcaster** — sends a HELLO packet every few seconds on the LAN
    broadcast address so other peers know we exist.
  • **Listener** — receives HELLO / GOODBYE packets and maintains a
    thread-safe ``PeerRegistry``.

Peers that haven't sent a HELLO within ``EXPIRY_SECONDS`` are pruned
automatically.
"""

import json
import socket
import threading
import time
from typing import Callable

from protocol import MsgType, build_hello, build_goodbye

# ── Defaults ───────────────────────────────────────────────────────────────────

BROADCAST_INTERVAL = 3        # seconds between HELLO broadcasts
EXPIRY_SECONDS     = 12       # drop peer after this many seconds of silence
BUFFER_SIZE        = 4096


# ── Peer Registry ─────────────────────────────────────────────────────────────

class PeerRegistry:
    """Thread-safe store of discovered peers."""

    def __init__(self, own_uuid: str):
        self._lock = threading.Lock()
        self._peers: dict[str, dict] = {}   # uuid → peer info
        self._own_uuid = own_uuid

    def update(self, uuid: str, username: str, ip: str, tcp_port: int):
        if uuid == self._own_uuid:
            return  # ignore ourselves
        with self._lock:
            self._peers[uuid] = {
                "uuid": uuid,
                "username": username,
                "ip": ip,
                "tcp_port": tcp_port,
                "last_seen": time.time(),
            }

    def remove(self, uuid: str):
        with self._lock:
            self._peers.pop(uuid, None)

    def prune(self):
        """Remove peers not heard from recently."""
        cutoff = time.time() - EXPIRY_SECONDS
        with self._lock:
            stale = [u for u, p in self._peers.items() if p["last_seen"] < cutoff]
            for u in stale:
                del self._peers[u]

    def list(self) -> list[dict]:
        """Return a snapshot of all live peers, sorted by username."""
        self.prune()
        with self._lock:
            return sorted(self._peers.values(), key=lambda p: p["username"].lower())

    def get_by_index(self, index: int) -> dict | None:
        """1-based index into the sorted peer list."""
        peers = self.list()
        if 1 <= index <= len(peers):
            return peers[index - 1]
        return None


# ── Broadcaster thread ────────────────────────────────────────────────────────

class Broadcaster(threading.Thread):
    """Periodically UDP-broadcasts HELLO on the LAN."""

    def __init__(self, uuid: str, username: str, tcp_port: int, udp_port: int):
        super().__init__(daemon=True, name="UDPBroadcaster")
        self.uuid = uuid
        self.username = username
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self._stop_event = threading.Event()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1)

        hello = json.dumps(build_hello(self.uuid, self.username, self.tcp_port)).encode("utf-8")

        while not self._stop_event.is_set():
            try:
                sock.sendto(hello, ("<broadcast>", self.udp_port))
            except OSError:
                pass  # network hiccup
            self._stop_event.wait(BROADCAST_INTERVAL)

        # Send GOODBYE before exiting
        try:
            goodbye = json.dumps(build_goodbye(self.uuid)).encode("utf-8")
            sock.sendto(goodbye, ("<broadcast>", self.udp_port))
        except OSError:
            pass
        sock.close()

    def stop(self):
        self._stop_event.set()


# ── Listener thread ──────────────────────────────────────────────────────────

class Listener(threading.Thread):
    """Listens for UDP HELLO / GOODBYE and updates the PeerRegistry."""

    def __init__(self, udp_port: int, registry: PeerRegistry,
                 on_new_peer: Callable[[dict], None] | None = None):
        super().__init__(daemon=True, name="UDPListener")
        self.udp_port = udp_port
        self.registry = registry
        self.on_new_peer = on_new_peer
        self._stop_event = threading.Event()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Allow multiple listeners on the same port (for same-machine testing)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", self.udp_port))
        sock.settimeout(1)

        while not self._stop_event.is_set():
            try:
                data, (ip, _port) = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                msg = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            msg_type = msg.get("type")

            if msg_type == MsgType.HELLO:
                uuid     = msg["uuid"]
                username = msg["username"]
                tcp_port = msg["tcp_port"]
                # Check if this is a genuinely new peer
                known = {p["uuid"] for p in self.registry.list()}
                self.registry.update(uuid, username, ip, tcp_port)
                if uuid not in known and self.on_new_peer:
                    self.on_new_peer({"uuid": uuid, "username": username,
                                     "ip": ip, "tcp_port": tcp_port})

            elif msg_type == MsgType.GOODBYE:
                self.registry.remove(msg.get("uuid", ""))

        sock.close()

    def stop(self):
        self._stop_event.set()

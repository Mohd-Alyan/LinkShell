"""
chat.py — TCP chat server, client, and session management.

• ``ChatServer``  — daemon thread that accepts incoming TCP connections and
  dispatches CHAT_REQUEST messages to a callback so the main thread can
  prompt the user to accept / decline.

• ``ChatSession`` — wraps a connected socket and runs a receive-loop thread.
  Incoming messages are printed via ``ui`` helpers.  Outgoing messages are
  sent from the caller's thread (main input loop).

• ``connect_to_peer()`` — opens a TCP connection to a peer, sends a
  CHAT_REQUEST, and waits for CHAT_ACCEPT / CHAT_DECLINE.
"""

import socket
import threading
from typing import Callable

from protocol import (
    send_message,
    recv_message,
    build_chat_request,
    build_chat_accept,
    build_chat_decline,
    build_chat_end,
    build_message,
    MsgType,
)
import transfer
import ui
import config

def _derive_key(priv_hex: str, pub_hex: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    
    priv_key = x25519.X25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    pub_key = x25519.X25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    shared_key = priv_key.exchange(pub_key)
    
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b'linkshell'
    ).derive(shared_key)


# ── Chat Session ──────────────────────────────────────────────────────────────

class ChatSession:
    """An active 1-on-1 chat over a connected TCP socket."""

    def __init__(self, sock: socket.socket, peer_name: str, peer_ip: str,
                 derived_key: bytes | None = None, on_ended: Callable | None = None,
                 is_client: bool = False):
        self.sock = sock
        self.peer_name = peer_name
        self.peer_ip = peer_ip
        self.derived_key = derived_key
        self.active = True
        self.reconnecting = False
        self.is_client = is_client
        self._on_ended = on_ended
        self._recv_thread: threading.Thread | None = None

        # File transfer state
        self.pending_file: dict | None = None
        self.file_decision_event = threading.Event()
        self.file_decision: str | None = None
        
        self.file_reply_event = threading.Event()
        self.file_reply: str | None = None
        self.file_reply_offset: int = 0
        self.file_ack_event = threading.Event()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        """Start the background receive loop."""
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="ChatRecv"
        )
        self._recv_thread.start()

    def close(self):
        """Gracefully shut down the session."""
        if not self.active:
            return
        self.active = False
        try:
            send_message(self.sock, build_chat_end(), self.derived_key)
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def swap_socket(self, new_sock: socket.socket, derived_key: bytes):
        """Hot-swap the underlying socket after a successful reconnect."""
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = new_sock
        self.derived_key = derived_key
        self.reconnecting = False
        # If this side wasn't the client, the recv thread probably died, so restart it
        if not self._recv_thread or not self._recv_thread.is_alive():
            self.start()

    # ── send helpers ───────────────────────────────────────────────────────

    def send_text(self, text: str):
        if not self.active:
            return
        if self.reconnecting:
            ui.print_error("Message failed: Reconnecting...")
            return
        try:
            send_message(self.sock, build_message(text), self.derived_key)
            ui.print_own_message(text)
        except OSError:
            ui.print_error("Failed to send — connection lost")
            self.active = False

    def send_file(self, filepath: str):
        if not self.active:
            return
        if self.reconnecting:
            ui.print_error("Transfer failed: Reconnecting...")
            return
        transfer.send_file(self, filepath)

    def accept_file(self, offset: int = 0):
        """Accept a pending file request."""
        if not self.pending_file:
            return
        self.file_decision = "ACCEPT"
        self.file_reply_offset = offset
        self.file_decision_event.set()

    def decline_file(self):
        """Decline a pending file request."""
        if not self.pending_file:
            return
        self.file_decision = "DECLINE"
        self.file_decision_event.set()

    # ── receive loop (runs in background thread) ──────────────────────────

    def _recv_loop(self):
        while self.active:
            try:
                msg = recv_message(self.sock, self.derived_key)
            except (OSError, ConnectionError):
                msg = None

            if msg is None:
                if self.active and not self.reconnecting:
                    ui.print_error(f"Connection lost! Reconnecting to {self.peer_name}...")
                    self.reconnecting = True
                    
                    if self.is_client:
                        # Client loops reconnect attempts
                        import time
                        while self.reconnecting and self.active:
                            # We need cfg to reconnect, we'll grab it from active_session via main?
                            # For simplicity, connect_to_peer expects cfg. We will import main.cfg
                            import main
                            if main.cfg is None: break
                            peer_tcp_port = main.registry.get_by_ip(self.peer_ip).get("tcp_port", 9877) if main.registry.get_by_ip(self.peer_ip) else 9877
                            
                            sock, dkey = connect_to_peer(self.peer_ip, peer_tcp_port, main.cfg, is_reconnect=True)
                            if sock:
                                self.swap_socket(sock, dkey)
                                ui.print_success("Reconnected successfully!")
                                break
                            time.sleep(3)
                    else:
                        # Server just waits for client to reconnect
                        break # Let the thread die, swap_socket will revive it
                break

            msg_type = msg.get("type")

            if msg_type == MsgType.MESSAGE:
                ui.print_peer_message(self.peer_name, msg.get("text", ""))

            elif msg_type == MsgType.FILE_META:
                self.pending_file = msg
                self.file_decision_event.clear()
                
                ui.print_file_request(
                    f"{self.peer_name} is sending a file: "
                    f"{msg.get('filename')} "
                    f"({msg.get('filesize', 0) / 1024 / 1024:.1f} MB). "
                    f"Type /accept or /decline"
                )
                
                # Block the receive loop until user decides
                self.file_decision_event.wait()
                
                meta = self.pending_file
                self.pending_file = None
                
                if self.file_decision == "ACCEPT":
                    from protocol import build_file_accept
                    try:
                        send_message(self.sock, build_file_accept(self.file_reply_offset), self.derived_key)
                        transfer.receive_file(self.sock, meta, self.peer_name, self.derived_key, self.file_reply_offset)
                    except OSError:
                        pass
                else:
                    from protocol import build_file_decline
                    try:
                        send_message(self.sock, build_file_decline(), self.derived_key)
                    except OSError:
                        pass

            elif msg_type == MsgType.FILE_ACCEPT:
                self.file_reply = "ACCEPT"
                self.file_reply_offset = msg.get("offset", 0)
                self.file_reply_event.set()

            elif msg_type == MsgType.FILE_DECLINE:
                self.file_reply = "DECLINE"
                self.file_reply_event.set()

            elif msg_type == MsgType.FILE_ACK:
                self.file_ack_event.set()

            elif msg_type == MsgType.CHAT_END:
                self.active = False
                ui.print_system(f"{self.peer_name} left the chat.")
                if self._on_ended:
                    self._on_ended()
                break


# ── Chat Server ───────────────────────────────────────────────────────────────

class ChatServer(threading.Thread):
    """TCP server that listens for incoming chat requests.

    Parameters
    ----------
    cfg : config.UserConfig
        Our configuration holding UUID, username, and keys.
    on_request : callable(sock, addr, peer_uuid, peer_username, peer_pubkey) -> bool | None
        Called when a CHAT_REQUEST arrives.  Return True (accept),
        False (decline), or None (deferred — the callback has stored
        the socket and will handle accept/decline later).
    """

    def __init__(self, cfg: config.UserConfig, on_request: Callable):
        super().__init__(daemon=True, name="TCPChatServer")
        self.cfg = cfg
        self.on_request = on_request
        self._stop_event = threading.Event()
        self._server_sock: socket.socket | None = None

    def run(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.settimeout(1)
        self._server_sock.bind(("", self.cfg.tcp_port))
        self._server_sock.listen(5)

        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            # Handle in a short-lived thread so the server stays responsive
            threading.Thread(
                target=self._handle_connection,
                args=(conn, addr),
                daemon=True,
                name="ChatReqHandler",
            ).start()

        if self._server_sock:
            self._server_sock.close()

    def _handle_connection(self, conn: socket.socket, addr: tuple):
        """Read the first message; if it's a CHAT_REQUEST, invoke callback."""
        try:
            msg = recv_message(conn)
        except (OSError, ConnectionError):
            conn.close()
            return

        if msg is None or msg.get("type") not in (MsgType.CHAT_REQUEST, MsgType.CHAT_RECONNECT):
            conn.close()
            return

        is_reconnect = msg.get("type") == MsgType.CHAT_RECONNECT
        peer_uuid = msg.get("uuid", "")
        peer_username = msg.get("username", "unknown")
        peer_pubkey = msg.get("public_key", "")

        result = self.on_request(conn, addr, peer_uuid, peer_username, peer_pubkey, is_reconnect)

        if result is True:
            send_message(conn, build_chat_accept(self.cfg.uuid, self.cfg.username, self.cfg.public_key))
            # Socket ownership transfers to the caller (via on_request callback)
        elif result is False:
            send_message(conn, build_chat_decline(self.cfg.uuid))
            conn.close()
        # else: result is None → deferred.  The callback stored the socket
        #       and the main thread will send accept/decline later.

    def stop(self):
        self._stop_event.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass


# ── Client-side: initiate chat ────────────────────────────────────────────────

def connect_to_peer(peer_ip: str, peer_tcp_port: int,
                    cfg: config.UserConfig,
                    timeout: float = 15.0,
                    is_reconnect: bool = False) -> tuple[socket.socket, bytes] | tuple[None, None]:
    """Open a TCP connection to *peer*, send CHAT_REQUEST, wait for reply.

    Returns (sock, derived_key) on CHAT_ACCEPT, or (None, None) on decline / error.
    """
    try:
        from protocol import build_chat_reconnect
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((peer_ip, peer_tcp_port))
        if is_reconnect:
            send_message(sock, build_chat_reconnect(cfg.uuid, cfg.username, cfg.public_key))
        else:
            send_message(sock, build_chat_request(cfg.uuid, cfg.username, cfg.public_key))

        reply = recv_message(sock)
        if reply and reply.get("type") == MsgType.CHAT_ACCEPT:
            peer_pubkey = reply.get("public_key", "")
            derived_key = _derive_key(cfg.private_key, peer_pubkey)
            sock.settimeout(None)
            return sock, derived_key
        else:
            reason = "declined" if reply else "no response"
            ui.print_system(f"Chat request {reason}.")
            sock.close()
            return None, None

    except (OSError, ConnectionError) as e:
        ui.print_error(f"Could not connect: {e}")
        return None, None

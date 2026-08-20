"""
protocol.py — Message framing and types for LAN Chat.
Wire format:  [4-byte big-endian length][JSON payload]
All messages are JSON dicts with at least a "type" field.
File data chunks carry base64-encoded binary in the "data" field.
"""
import json
import struct
import base64
import socket
import os
from enum import Enum
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
class MsgType(str, Enum):
    HELLO       = "HELLO"
    GOODBYE     = "GOODBYE"
    CHAT_REQUEST  = "CHAT_REQUEST"
    CHAT_ACCEPT   = "CHAT_ACCEPT"
    CHAT_DECLINE  = "CHAT_DECLINE"
    CHAT_RECONNECT = "CHAT_RECONNECT"
    CHAT_END      = "CHAT_END"
    MESSAGE = "MESSAGE"
    FILE_META   = "FILE_META"
    FILE_ACCEPT = "FILE_ACCEPT"
    FILE_DECLINE = "FILE_DECLINE"
    FILE_DATA   = "FILE_DATA"
    FILE_END    = "FILE_END"
    FILE_ACK    = "FILE_ACK"
HEADER_FMT  = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_MSG_SIZE = 10 * 1024 * 1024
def _recvall(sock: socket.socket, n: int) -> bytes | None:
    """Receive exactly *n* bytes from *sock*.  Returns None on disconnect."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
def send_message(sock: socket.socket, msg: dict, key: bytes | None = None) -> None:
    """Serialize *msg* as length-prefixed JSON (optionally AES-GCM encrypted) and send it over *sock*."""
    payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    if key is not None:
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        payload = nonce + aesgcm.encrypt(nonce, payload, None)
    header  = struct.pack(HEADER_FMT, len(payload))
    sock.sendall(header + payload)
def recv_message(sock: socket.socket, key: bytes | None = None) -> dict | None:
    """Read one length-prefixed JSON message from *sock* (optionally AES-GCM decrypted).
    Returns the parsed dict, or ``None`` on clean disconnect.
    Raises ``ConnectionError`` on protocol violations or decryption failures.
    """
    raw_header = _recvall(sock, HEADER_SIZE)
    if raw_header is None:
        return None
    (length,) = struct.unpack(HEADER_FMT, raw_header)
    if length > MAX_MSG_SIZE:
        raise ConnectionError(f"Message too large: {length} bytes")
    raw_payload = _recvall(sock, length)
    if raw_payload is None:
        return None
    if key is not None:
        if len(raw_payload) < 12:
            raise ConnectionError("Encrypted payload too short (missing nonce)")
        aesgcm = AESGCM(key)
        nonce = raw_payload[:12]
        ct = raw_payload[12:]
        try:
            raw_payload = aesgcm.decrypt(nonce, ct, None)
        except Exception as e:
            raise ConnectionError(f"Decryption failed: {e}")
    return json.loads(raw_payload.decode("utf-8"))
def build_hello(uuid: str, username: str, tcp_port: int) -> dict:
    return {"type": MsgType.HELLO, "uuid": uuid, "username": username, "tcp_port": tcp_port}
def build_goodbye(uuid: str) -> dict:
    return {"type": MsgType.GOODBYE, "uuid": uuid}
def build_chat_request(uuid: str, username: str, public_key: str) -> dict:
    return {"type": MsgType.CHAT_REQUEST, "uuid": uuid, "username": username, "public_key": public_key}
def build_chat_reconnect(uuid: str, username: str, public_key: str) -> dict:
    return {"type": MsgType.CHAT_RECONNECT, "uuid": uuid, "username": username, "public_key": public_key}
def build_chat_accept(uuid: str, username: str, public_key: str) -> dict:
    return {"type": MsgType.CHAT_ACCEPT, "uuid": uuid, "username": username, "public_key": public_key}
def build_chat_decline(uuid: str, reason: str = "") -> dict:
    return {"type": MsgType.CHAT_DECLINE, "uuid": uuid, "reason": reason}
def build_chat_end() -> dict:
    return {"type": MsgType.CHAT_END}
def build_message(text: str) -> dict:
    return {"type": MsgType.MESSAGE, "text": text}
def build_file_meta(filename: str, filesize: int) -> dict:
    return {"type": MsgType.FILE_META, "filename": filename, "filesize": filesize}
def build_file_accept(offset: int = 0) -> dict:
    return {"type": MsgType.FILE_ACCEPT, "offset": offset}
def build_file_decline() -> dict:
    return {"type": MsgType.FILE_DECLINE}
def build_file_data(data: bytes) -> dict:
    return {"type": MsgType.FILE_DATA, "data": base64.b64encode(data).decode("ascii")}
def build_file_end(checksum: str = "") -> dict:
    return {"type": MsgType.FILE_END, "checksum": checksum}
def build_file_ack(status: str = "received") -> dict:
    return {"type": MsgType.FILE_ACK, "status": status}

"""
transfer.py — File send / receive over an active TCP chat socket.
Sending:
  1. FILE_META  →  {filename, filesize}
  2. FILE_DATA  →  {data: base64}   (repeated, 64 KB chunks)
  3. FILE_END   →  {}
  4. ← FILE_ACK  {status}
Receiving is driven by the chat session's message loop — it calls
``receive_file()`` when it sees a FILE_META message.
"""
import base64
import os
import hashlib
from pathlib import Path
from protocol import (
    send_message,
    recv_message,
    build_file_meta,
    build_file_data,
    build_file_end,
    build_file_ack,
    MsgType,
)
import ui
CHUNK_SIZE = 64 * 1024
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_FILE_SIZE = 100 * 1024 * 1024
def send_file(session, filepath: str) -> bool:
    """Send a file over *session.sock*.  Returns True on success."""
    path = Path(filepath)
    if not path.is_file():
        ui.print_error(f"File not found: {filepath}")
        return False
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        ui.print_error(f"File too large ({size / 1024 / 1024:.1f} MB). "
                       f"Max is {MAX_FILE_SIZE / 1024 / 1024:.0f} MB.")
        return False
    filename = path.name
    session.file_reply_event.clear()
    session.file_reply = None
    session.file_ack_event.clear()
    send_message(session.sock, build_file_meta(filename, size), session.derived_key)
    ui.print_system(f"Waiting for {session.peer_name} to accept the file...")
    if not session.file_reply_event.wait(60):
        ui.print_error("File transfer timed out waiting for reply")
        return False
    if session.file_reply != "ACCEPT":
        ui.print_error(f"File transfer declined by {session.peer_name}")
        return False
    sent = session.file_reply_offset
    file_hash = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            if sent > 0:
                f.seek(sent)
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                file_hash.update(chunk)
                send_message(session.sock, build_file_data(chunk), session.derived_key)
                sent += len(chunk)
                ui.print_progress(f"Sending {filename}", sent, size)
    except OSError as e:
        ui.print_error(f"Read error: {e}")
        return False
    send_message(session.sock, build_file_end(file_hash.hexdigest()), session.derived_key)
    if session.file_ack_event.wait(30):
        ui.print_success(f"File sent: {filename} "
                         f"({size / 1024 / 1024:.1f} MB)")
        return True
    ui.print_error("No acknowledgement received")
    return False
def receive_file(sock, meta: dict, sender_name: str, derived_key: bytes | None, offset: int = 0) -> bool:
    """Receive a file after FILE_META has already been parsed.
    Reads FILE_DATA chunks from *sock* until FILE_END, then sends FILE_ACK.
    Returns True on success.
    """
    filename = meta.get("filename", "unknown")
    filesize = meta.get("filesize", 0)
    receive_dir = Path.home() / "Downloads" / "linkshell" / sender_name
    receive_dir.mkdir(parents=True, exist_ok=True)
    dest = receive_dir / filename
    if offset == 0:
        counter = 1
        while dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            dest = receive_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    received = offset
    file_hash = hashlib.sha256()
    mode = "ab" if offset > 0 else "wb"
    try:
        with open(dest, mode) as f:
            while True:
                msg = recv_message(sock, derived_key)
                if msg is None:
                    ui.print_error("Connection lost during file transfer")
                    return False
                msg_type = msg.get("type")
                if msg_type == MsgType.FILE_DATA:
                    chunk = base64.b64decode(msg["data"])
                    file_hash.update(chunk)
                    f.write(chunk)
                    received += len(chunk)
                    ui.print_progress(f"Receiving {filename}", received,
                                      filesize or received)
                elif msg_type == MsgType.FILE_END:
                    expected_checksum = msg.get("checksum", "")
                    if expected_checksum and expected_checksum != file_hash.hexdigest():
                        ui.print_error(f"Checksum mismatch! Transfer corrupted.")
                        try:
                            send_message(sock, build_file_ack("failed"), derived_key)
                        except OSError:
                            pass
                        f.close()
                        dest.unlink()
                        return False
                    break
                else:
                    continue
    except OSError as e:
        ui.print_error(f"Write error: {e}")
        return False
    try:
        send_message(sock, build_file_ack("received"), derived_key)
    except OSError:
        pass
    ui.print_success(f"File received: {dest.name} → {dest}")
    return True

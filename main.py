"""
main.py — Entry point for LinkShell.

Usage
-----
    python main.py                  # default ports
    python main.py --tcp 9877 --udp 9876
    python main.py --tcp 9878 --udp 9877   # second instance on same machine

Flow
----
1. First run → register (pick a username, generate UUID).
2. Start UDP discovery + TCP chat server.
3. Main menu loop: list peers, /chat, /refresh, /quit.
4. Chat interface: messages, /send <file>, /exit.
"""

import argparse
import sys
import threading
import time

import config
import ui
from discovery import Broadcaster, Listener, PeerRegistry
from chat import ChatServer, ChatSession, connect_to_peer


# ── Globals ────────────────────────────────────────────────────────────────────

cfg: config.UserConfig = None          # type: ignore[assignment]
registry: PeerRegistry = None          # type: ignore[assignment]
broadcaster: Broadcaster = None        # type: ignore[assignment]
listener: Listener = None              # type: ignore[assignment]
chat_server: ChatServer = None         # type: ignore[assignment]

# Active chat session (only one at a time)
active_session: ChatSession | None = None
session_lock = threading.Lock()

# Pending incoming chat request — set by server thread, consumed by main thread.
# The server thread returns None (deferred) so it does NOT try to call input().
_pending_request: dict | None = None
_pending_lock = threading.Lock()


# ── Registration ───────────────────────────────────────────────────────────────

def do_registration(tcp_port: int, udp_port: int) -> config.UserConfig:
    ui.print_banner()
    ui.console.print("  [bold]Welcome to LinkShell![/bold]")
    ui.console.print("  [dim]Let's set up your identity.[/dim]")
    print()
    while True:
        username = ui.prompt("Choose a username: ")
        if username and not username.startswith("/"):
            break
        ui.print_error("Please enter a valid username.")

    cfg = config.register(username, tcp_port, udp_port)
    ui.print_success(f"Registered as [bold]{cfg.username}[/bold] [dim](id: {cfg.uuid[:8]}...)[/dim]")
    time.sleep(1)
    return cfg


# ── Incoming chat request handler (called from ChatServer thread) ─────────────

def on_chat_request(conn, addr, peer_uuid: str, peer_username: str, peer_pubkey: str, is_reconnect: bool = False):
    """Called from the TCP server thread when someone wants to chat with us.

    We stash the request for the main thread and return None (deferred)
    so the server thread does NOT try to read stdin — that would deadlock
    because the main thread is already blocking on input().
    """
    global _pending_request

    if is_reconnect:
        with session_lock:
            if active_session is not None and active_session.peer_name == peer_username:
                from protocol import send_message, build_chat_accept
                from chat import _derive_key
                try:
                    send_message(conn, build_chat_accept(cfg.uuid, cfg.username, cfg.public_key))
                    derived_key = _derive_key(cfg.private_key, peer_pubkey)
                    active_session.swap_socket(conn, derived_key)
                    ui.print_success(f"{peer_username} reconnected seamlessly!")
                except OSError:
                    conn.close()
                return None
        return False

    with session_lock:
        if active_session is not None:
            # Already in a chat — decline automatically
            from protocol import send_message, build_chat_decline
            try:
                send_message(conn, build_chat_decline(cfg.uuid, reason="busy"))
            except:
                pass
            conn.close()
            ui.print_notification(f"{peer_username} requested you for a chat! Please leave the current chat to join.")
            return None

    with _pending_lock:
        if _pending_request is not None:
            # Another request is already pending — decline this one
            return False

        # Stash the request (including the open socket) for the main thread
        _pending_request = {
            "conn": conn,
            "addr": addr,
            "peer_uuid": peer_uuid,
            "peer_username": peer_username,
            "peer_pubkey": peer_pubkey,
        }

    # Notify the user — they can type /accept or /decline at the >>> prompt
    ui.print_notification(
        f"{peer_username}@{addr[0]} wants to chat!  "
        f"Type /accept or /decline"
    )

    # Return None → deferred.  Server thread won't send accept/decline.
    return None


# ── Chat interface loop ──────────────────────────────────────────────────────

def chat_loop(session: ChatSession):
    """Run the interactive chat until /exit or disconnect."""
    global active_session

    ui.print_chat_header(session.peer_name, session.peer_ip)
    ui.enter_chat_mode()

    while session.active:
        try:
            text = ui.prompt("> ")
        except (EOFError, KeyboardInterrupt):
            text = "/quit"

        if not text:
            continue

        if text.lower() in ("/quit", "/leave chat"):
            ui.leave_chat_mode()
            session.close()
            break

        if text.lower().startswith("/file "):
            filepath = text[6:].strip().strip('"').strip("'")
            if filepath:
                session.send_file(filepath)
            else:
                ui.print_error("Usage: /file <filepath>")
            continue

        if text.lower() == "/accept":
            if session.pending_file:
                from pathlib import Path
                dest = Path.home() / "Downloads" / "linkshell" / session.peer_name / session.pending_file["filename"]
                offset = 0
                if dest.exists():
                    size = dest.stat().st_size
                    if size < session.pending_file.get("filesize", 0):
                        offset = size
                        ui.print_system(f"Resuming file from {offset} bytes...")
                    else:
                        ui.print_system("File already exists. Saving as copy.")
                else:
                    ui.print_success("Accepting file transfer...")
                session.accept_file(offset)
            else:
                ui.print_error("No pending file request.")
            continue

        if text.lower() == "/decline":
            if session.pending_file:
                ui.print_system(f"Declining file transfer...")
                session.decline_file()
            else:
                ui.print_error("No pending file request.")
            continue

        # Regular text message
        session.send_text(text)

    ui.leave_chat_mode()
    ui.print_chat_ended()

    with session_lock:
        active_session = None


# ── Main menu loop ────────────────────────────────────────────────────────────

def main_menu():
    global active_session, _pending_request

    while True:
        # Draw the menu
        ui.print_banner()
        ui.print_status_bar(cfg.username, cfg.uuid[:8])
        peers = registry.list()
        ui.print_peers(peers)
        ui.print_menu()

        # Show pending request hint if one exists
        with _pending_lock:
            has_pending = _pending_request is not None
        if has_pending:
            ui.print_notification_inline(
                "You have a pending chat request!  "
                "Type /accept or /decline"
            )

        cmd = ui.prompt()

        if not cmd:
            continue

        # ── /quit ──────────────────────────────────────────────────────────
        if cmd.lower() == "/quit":
            # Decline any pending request before quitting
            with _pending_lock:
                req = _pending_request
                _pending_request = None
            if req:
                try:
                    from protocol import send_message, build_chat_decline
                    send_message(req["conn"], build_chat_decline(cfg.uuid))
                    req["conn"].close()
                except OSError:
                    pass
            ui.print_system("Shutting down …")
            shutdown()
            break

        # ── /accept ────────────────────────────────────────────────────────
        if cmd.lower() == "/accept":
            with _pending_lock:
                req = _pending_request
                _pending_request = None
            if req is None:
                ui.print_error("No pending chat request.")
                time.sleep(1)
                continue
            # Send CHAT_ACCEPT on the stashed socket
            from protocol import send_message, build_chat_accept
            try:
                send_message(req["conn"], build_chat_accept(cfg.uuid, cfg.username, cfg.public_key))
                from chat import _derive_key
                derived_key = _derive_key(cfg.private_key, req["peer_pubkey"])
            except OSError:
                ui.print_error("Connection lost — request expired.")
                time.sleep(1)
                continue

            ui.print_success(f"Chat with {req['peer_username']} accepted!")

            session = ChatSession(
                sock=req["conn"],
                peer_name=req["peer_username"],
                peer_ip=req["addr"][0],
                derived_key=derived_key,
                is_client=False
            )
            with session_lock:
                active_session = session
            session.start()
            chat_loop(session)
            continue

        # ── /decline ───────────────────────────────────────────────────────
        if cmd.lower() == "/decline":
            with _pending_lock:
                req = _pending_request
                _pending_request = None
            if req is None:
                ui.print_error("No pending chat request.")
                time.sleep(1)
                continue
            from protocol import send_message, build_chat_decline
            try:
                send_message(req["conn"], build_chat_decline(cfg.uuid))
                req["conn"].close()
            except OSError:
                pass
            ui.print_system("Chat request declined.")
            time.sleep(1)
            continue

        # ── /refresh ───────────────────────────────────────────────────────
        if cmd.lower() == "/refresh":
            ui.print_system("Rescanning …")
            time.sleep(2)  # give a broadcast cycle time
            continue

        # ── /chat <n> ──────────────────────────────────────────────────────
        if cmd.lower().startswith("/chat"):
            parts = cmd.split()
            if len(parts) != 2 or not parts[1].isdigit():
                ui.print_error("Usage: /chat <peer_number>")
                time.sleep(1)
                continue

            idx = int(parts[1])
            peer = registry.get_by_index(idx)
            if peer is None:
                ui.print_error(f"Invalid peer number: {idx}")
                time.sleep(1)
                continue

            ui.print_system(
                f"Sending chat request to {peer['username']}@{peer['ip']} …"
            )
            sock, derived_key, reason = connect_to_peer(
                peer["ip"], peer["tcp_port"], cfg
            )
            if sock is None:
                if reason == "busy":
                    ui.print_system(f"{peer['username']} is busy. Your request was sent, please wait.")
                else:
                    ui.print_error("Chat request declined.")
                time.sleep(2)
                continue

            ui.print_success(f"{peer['username']} accepted!")

            session = ChatSession(
                sock=sock,
                peer_name=peer["username"],
                peer_ip=peer["ip"],
                derived_key=derived_key,
                is_client=True
            )
            with session_lock:
                active_session = session
            session.start()
            chat_loop(session)
            continue

        # ── unknown command ────────────────────────────────────────────────
        ui.print_error(f"Unknown command: {cmd}")
        time.sleep(1)


# ── Start / Stop ──────────────────────────────────────────────────────────────

def start_services():
    global broadcaster, listener, chat_server, registry

    registry = PeerRegistry(cfg.uuid)

    broadcaster = Broadcaster(cfg.uuid, cfg.username, cfg.tcp_port, cfg.udp_port)
    listener = Listener(cfg.udp_port, registry)
    chat_server = ChatServer(cfg, on_chat_request)

    broadcaster.start()
    listener.start()
    chat_server.start()


def shutdown():
    broadcaster.stop()
    listener.stop()
    chat_server.stop()
    # Give threads a moment to send GOODBYE
    time.sleep(0.5)


# ── CLI entry ─────────────────────────────────────────────────────────────────

def main():
    global cfg

    parser = argparse.ArgumentParser(description="LinkShell — Office Messenger")
    parser.add_argument("--tcp", type=int, default=config.DEFAULT_TCP_PORT,
                        help=f"TCP port for chat (default {config.DEFAULT_TCP_PORT})")
    parser.add_argument("--udp", type=int, default=config.DEFAULT_UDP_PORT,
                        help=f"UDP port for discovery (default {config.DEFAULT_UDP_PORT})")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to a custom config directory (useful for running multiple instances on one machine)")
    parser.add_argument("--reset", action="store_true",
                        help="Re-register (pick new username, keep UUID)")
    args = parser.parse_args()

    if args.config:
        from pathlib import Path
        config.CONFIG_DIR = Path(args.config)
        config.CONFIG_FILE = config.CONFIG_DIR / "config.json"
        config.PEERS_FILE = config.CONFIG_DIR / "peers.json"

    # ── Registration ───────────────────────────────────────────────────────
    if config.config_exists() and not args.reset:
        cfg = config.load_config()
        # Override ports from CLI
        cfg.tcp_port = args.tcp
        cfg.udp_port = args.udp
    else:
        cfg = do_registration(args.tcp, args.udp)

    # ── Start networking ───────────────────────────────────────────────────
    try:
        start_services()
    except OSError as e:
        ui.print_error(f"Could not start services: {e}")
        ui.print_error("Is another instance already running on the same ports?")
        sys.exit(1)

    ui.print_banner()
    ui.print_system(f"Logged in as [bold]{cfg.username}[/bold] [dim](TCP:{cfg.tcp_port}  UDP:{cfg.udp_port})[/dim]")
    ui.print_system("Scanning for peers …")
    time.sleep(3)  # wait for first discovery cycle

    # ── Main loop ──────────────────────────────────────────────────────────
    try:
        main_menu()
    except KeyboardInterrupt:
        ui.print_system("Interrupted — shutting down …")
        shutdown()


if __name__ == "__main__":
    main()

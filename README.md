# 🏢 LinkShell — A decentralized peer-to-peer chat app

**Made by Alyan and Ifra**

A clean, serverless, terminal-based chat application designed for local networks.
It automatically discovers peers via UDP broadcast and establishes End-to-End Encrypted (X25519 + AES-GCM) TCP connections for chat and large file transfers.

## Features

- **Persistent Identity** — UUID generated at first registration, IP mapping refreshes every scan
- **Auto-Discovery** — UDP broadcast finds all peers on the LAN automatically
- **Chat Requests** — Send a request, other person accepts/declines
- **Real-time Messaging** — Full chat interface with timestamps
- **File Transfer** — Send files with progress bar via `/send <filepath>`
- **Styled Terminal UI** — ANSI colors, Unicode box-drawing, progress bars

## Quick Start

```bash
# First run — will ask for a username
python main.py

# With custom ports (for testing multiple instances on one machine)
python main.py --tcp 9877 --udp 9876    # Terminal 1
python main.py --tcp 9878 --udp 9876    # Terminal 2

# Re-register with a new username
python main.py --reset
```

## Commands

### Main Menu

| Command          | Description                    |
|------------------|--------------------------------|
| `/chat <number>` | Start a chat with a peer       |
| `/refresh`       | Rescan for online peers        |
| `/quit`          | Exit the application           |

### In Chat

| Command              | Description                     |
|----------------------|---------------------------------|
| `/file <filepath>`   | Send a file to the chat partner |
| `/quit` or `/leave chat` | Leave the chat, return to menu |

## How It Works

```
┌─────────┐    UDP Broadcast    ┌─────────┐
│  Peer A  │◄──────────────────►│  Peer B  │
│          │   (port 9876)      │          │
│          │                    │          │
│          │◄──────────────────►│          │
│          │   TCP Connection   │          │
│          │   (port 9877)      │          │
└─────────┘                    └─────────┘
```

1. On startup, each peer **broadcasts HELLO** via UDP every 3 seconds
2. Peers **discover each other** and appear in the online list
3. User sends a **chat request** → accepted/declined by the other peer
4. On accept, a **TCP connection** is established for messaging & file transfer
5. On exit, peer broadcasts **GOODBYE** and disappears from others' lists

## File Structure

```
main.py          — Entry point, menu loop, chat interface
config.py        — UUID generation, persistent config storage
discovery.py     — UDP broadcast/listen, peer registry
protocol.py      — Wire protocol (length-prefixed JSON)
chat.py          — TCP chat server/client, session management
transfer.py      — File send/receive with progress display
ui.py            — Terminal rendering, ANSI colors
```

## Configuration

Config is stored at `~/.lanchat/config.json`:

```json
{
  "uuid": "a1b2c3d4-...",
  "username": "alice",
  "tcp_port": 9877,
  "udp_port": 9876
}
```

Received files are saved to `~/lanchat_received/`.

## Requirements

- Python 3.10+
- Windows 10+, macOS, or Linux
- Computers on the same LAN subnet
- Firewall must allow UDP 9876 and TCP 9877

## Testing on a Single Machine

Open two terminal windows:

```bash
# Terminal 1
python main.py --tcp 9877 --udp 9876

# Terminal 2
python main.py --tcp 9878 --udp 9876 --reset
```

Both instances will discover each other and you can test all features locally.

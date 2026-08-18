"""
ui.py — Terminal rendering helpers with ANSI colors, upgraded with Rich.

Works on Windows 10+ (modern terminals), macOS, and Linux.
"""

import os
import sys
import shutil
import threading
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box

# Initialize Rich Console
console = Console(highlight=False)

def _enable_ansi_windows():
    """Enable VT100 escape sequences on Windows 10+."""
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

_enable_ansi_windows()

# ── Screen helpers ─────────────────────────────────────────────────────────────

def term_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def _hr(char="━", color="cyan"):
    w = term_width()
    console.print(f"[{color}]{char * w}[/{color}]")

# ── Thread-safe printing ──────────────────────────────────────────────────────

_chat_mode = False
_print_lock = threading.Lock()
_CHAT_PROMPT = f"  \033[1m\033[92m> \033[0m"    # bold green "> "
_MENU_PROMPT = f"  \033[1m\033[96m>>> \033[0m"   # bold cyan ">>> "


def enter_chat_mode():
    global _chat_mode
    _chat_mode = True

def leave_chat_mode():
    global _chat_mode
    _chat_mode = False

def _active_prompt() -> str:
    """Return the ANSI prompt string for the current mode."""
    return _CHAT_PROMPT if _chat_mode else _MENU_PROMPT


def _safe_rich_print(*args, **kwargs):
    """Print using Rich from any thread — clears the input prompt first, then restores it."""
    with _print_lock:
        if threading.current_thread() == threading.main_thread():
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()
            console.print(*args, **kwargs)
        else:
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()
            console.print(*args, **kwargs)
            sys.stdout.write(_active_prompt())
            sys.stdout.flush()

# ── Banner ─────────────────────────────────────────────────────────────────────

BANNER = r"""
  _      _       _     _____ _          _ _ 
 | |    (_)     | |   / ____| |        | | |
 | |     _ _ __ | | _| (___ | |__   ___| | |
 | |    | | '_ \| |/ /\___ \| '_ \ / _ \ | |
 | |____| | | | |   < ____) | | | |  __/ | |
 |______|_|_| |_|_|\_\_____/|_| |_|\___|_|_|
"""

def print_banner():
    clear_screen()
    panel = Panel(
        Align.center(f"[bold cyan]{BANNER}[/bold cyan]\n[dim]A decentralized peer-to-peer chat app[/dim]\n[bold magenta]Made by Alyan and Ifra[/bold magenta]"),
        border_style="cyan",
        box=box.DOUBLE_EDGE,
        padding=(1, 2)
    )
    console.print(panel)
    print()

# ── Main menu ─────────────────────────────────────────────────────────────────

def print_status_bar(username: str, uuid_short: str):
    from rich.rule import Rule
    console.print(Rule(f"🏢 LinkShell │ {username} │ id:{uuid_short}", style="bold blue"))
    print()


def print_peers(peers: list[dict]):
    if not peers:
        console.print("  [dim italic]No peers online. Waiting for someone to join...[/dim italic]")
        print()
        return

    table = Table(title="Online Peers", title_style="bold green", title_justify="left", box=box.SIMPLE, show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Username", style="green")
    table.add_column("IP Address", style="dim")
    table.add_column("ID", style="dim")

    for i, p in enumerate(peers, 1):
        uid_short = p["uuid"][:8]
        table.add_row(f"[{i}]", p["username"], p["ip"], f"({uid_short}...)")

    console.print("  ", end="")
    console.print(table)
    print()


def print_menu():
    table = Table(box=None, show_header=False)
    table.add_column("Command", style="bold yellow")
    table.add_column("Separator", style="dim")
    table.add_column("Description")
    
    cmds = [
        ("/chat <number>", "Start a chat with a peer"),
        ("/accept",        "Accept incoming chat request"),
        ("/decline",       "Decline incoming chat request"),
        ("/refresh",       "Rescan for peers"),
        ("/quit",          "Exit the app"),
    ]
    for cmd, desc in cmds:
        table.add_row(f"  {cmd}", "—", desc)
        
    console.print("  [bold]Commands:[/bold]")
    console.print(table)
    print()

# ── Chat interface ─────────────────────────────────────────────────────────────

def print_chat_header(peer_name: str, peer_ip: str):
    print()
    from rich.rule import Rule
    console.print(Rule(f"Chat with {peer_name}@{peer_ip}", style="cyan"))
    console.print(Align.center("[dim]/file <path> = send file   |   /quit = leave chat[/dim]"))
    console.print(Rule(style="cyan"))
    print()


def print_own_message(text: str):
    ts = datetime.now().strftime("%H:%M")
    console.print(f"  [dim]\\[{ts}][/dim] [bold green]you:[/bold green] {text}")


def print_peer_message(name: str, text: str):
    ts = datetime.now().strftime("%H:%M")
    _safe_rich_print(f"  [dim]\\[{ts}][/dim] [bold yellow]{name}:[/bold yellow] {text}")


def print_system(text: str):
    ts = datetime.now().strftime("%H:%M")
    _safe_rich_print(f"  [dim]\\[{ts}][/dim] [cyan]✦ {text}[/cyan]")


def print_error(text: str):
    _safe_rich_print(f"  [red]✗ {text}[/red]")


def print_success(text: str):
    _safe_rich_print(f"  [green]✓ {text}[/green]")

# ── File transfer progress ────────────────────────────────────────────────────

def print_progress(label: str, current: int, total: int):
    pct = current / total if total else 0
    size_mb = current / 1024 / 1024
    
    # Dynamically calculate width so it NEVER wraps the terminal
    w = term_width() - len(label) - 30
    w = max(10, min(50, w))
    
    filled = int(pct * w)
    bar = "█" * filled + "░" * (w - filled)

    with _print_lock:
        bar_str = f"  \033[96m⇄\033[0m {label}  \033[1m[{bar}]\033[0m  {pct:>6.1%}  ({size_mb:.1f} MB)"
        
        if threading.current_thread() == threading.main_thread():
            sys.stdout.write(f"\r\033[K{bar_str}")
            if current >= total:
                sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(f"\r\033[K{bar_str}")
            if current >= total:
                sys.stdout.write(f"\n{_active_prompt()}")
            sys.stdout.flush()

# ── Notifications ──────────────────────────────────────────────────────────────

def print_notification(text: str):
    """Thread-safe notification — clears the active input line and restores the prompt."""
    panel = Panel(f"[bold red]✦ {text}[/bold red]", border_style="red", expand=False)
    _safe_rich_print(panel)


def print_notification_inline(text: str):
    """Notification printed from the main thread (before input) — no prompt restoration."""
    panel = Panel(f"[bold red]✦ {text}[/bold red]", border_style="red", expand=False)
    console.print(panel)


def print_file_request(text: str):
    """Less intrusive notification for file requests."""
    _safe_rich_print(f"  [bold cyan]◆ {text}[/bold cyan]")

def print_chat_ended():
    print()
    from rich.rule import Rule
    console.print(Rule("Chat ended. Returning to main menu...", style="dim cyan"))
    print()


def prompt(label: str = ">>> ") -> str:
    """Read input with a styled prompt."""
    try:
        if label == ">>> ":
            return input(_MENU_PROMPT).strip()
        elif label == "> ":
            return input(_CHAT_PROMPT).strip()
        else:
            return input(f"  \033[1m\033[96m{label}\033[0m").strip()
    except (EOFError, KeyboardInterrupt):
        return "/quit"

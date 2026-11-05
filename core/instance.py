"""Single-instance guard.

Two copies of InvoiceWarden on one database would each run a watcher and could
upload the same invoice twice (e.g. the Windows startup entry plus a manual
launch). The first copy binds a loopback port; a second copy fails to bind and
exits. The OS frees the port if the owner crashes, so there is no stale lock.
"""
from __future__ import annotations

import socket

#: Arbitrary private port reserved for this app's lock.
LOCK_PORT = 50737


def acquire(port: int = LOCK_PORT) -> socket.socket | None:
    """Take the lock. Returns the held socket, or None if another copy has it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # No SO_REUSEADDR on Windows: it would let a second bind succeed.
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        sock.close()
        return None
    return sock

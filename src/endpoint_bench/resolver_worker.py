"""One-shot DNS helper, launched as a fresh isolated Python interpreter.

Input: hostname and numeric port only. Output: one JSON record. No model call,
credential lookup, retry, persistent service or import of the threaded engine.
"""

from __future__ import annotations

import json
import socket
import sys


def main() -> int:
    try:
        host, port_text = sys.argv[1:]
        port = int(port_text)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        record = {"addresses": [[i[0], i[1], i[2], list(i[4])] for i in infos]}
    except (OSError, ValueError) as error:
        record = {"error": f"{type(error).__name__}: {error}"}
    sys.stdout.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

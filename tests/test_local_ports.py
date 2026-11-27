"""Port checks before starting a local Compose project."""

import socket
import sys

import pytest

from rubato.local import LocalError, LocalStack


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_busy_port_is_reported(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(LocalError, match=str(port)):
            LocalStack(tmp_path).check_ports({"api": port}, {})


@pytest.mark.skipif(sys.platform == "win32", reason="TIME_WAIT reuse is a POSIX concern")
def test_port_left_in_time_wait_by_a_stopped_stack_is_free(tmp_path):
    # The CI benchmark starts right after the HA drill stops the same project: the
    # connections it served stay in TIME_WAIT on the published port for a minute.
    port = _free_port()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen()
    client = socket.create_connection(("127.0.0.1", port))
    served, _ = listener.accept()
    served.close()  # the server closes first, so its side enters TIME_WAIT
    client.recv(1)
    client.close()
    listener.close()
    with socket.socket() as plain:
        with pytest.raises(OSError):
            plain.bind(("127.0.0.1", port))
    LocalStack(tmp_path).check_ports({"api": port}, {})

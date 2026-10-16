import os
import sys
import json
import time
import socket
import threading
import subprocess

DAEMON_HOST = '127.0.0.1'
DAEMON_PORT = 5555
DAEMON_TIMEOUT = 5
D_HEARTBEAT_FREQUENCY = 60
BUFFER_SIZE = 4096  # 4 KB

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON_PATH = os.path.join(SCRIPT_DIR, 'ttl_daemon.py')

# checks if daemon is already running on 127.0.0.1:5555, if not spawns it in a new console window
def start_daemon():
    # try to connect 
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as d_socket:
            d_socket.settimeout(1)
            d_socket.connect((DAEMON_HOST, DAEMON_PORT))
            print('-_- TTL Daemon already running.')
            return
    except (ConnectionRefusedError, socket.timeout):
        print('-_- Daemon not running, spawning TTL Daemon...')

    subprocess.Popen(
        [sys.executable, DAEMON_PATH],
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
    )

    time.sleep(1.5) # allow Daemon to boot up
    print('-_- TTL Daemon spawned succesfully')

# connects over TCP, sends JSON payload, and drains the stream until EOF (b'') to prevent partial JSON truncation
def send_daemon_request(payload: dict) -> dict | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
            client_socket.settimeout(5)
            client_socket.connect((DAEMON_HOST, DAEMON_PORT))

            request_bytes = json.dumps(payload).encode('utf-8')
            client_socket.sendall(request_bytes)

            # keep reading chunks until the server closes connection (EOF)
            chunks = []
            while True:
                chunk = client_socket.recv(BUFFER_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)
            if not chunks:
                return None

            processed_request = json.loads(b''.join(chunks).decode('utf-8'))
            return processed_request

    except Exception as e:
        print(f'-_- [WARNING] Failed to communicate with TTL Daemon. {e}')
        return None

# sends a lightweight PING action to reset the daemon's inactivity clock
def ping_daemon() -> bool:
    resp = send_daemon_request({'action' : 'PING'})
    return resp is not None and resp.get('status', 'INVALID') == 'ok'

# runs in the background, pinging the daemon every 60s until stop_event is set
def heartbeat_worker(stop_event: threading.Event):
    # run in the background, pinging the daemon until asked to stop
    while not stop_event.is_set():
        if stop_event.wait(D_HEARTBEAT_FREQUENCY):
            break

        ping_daemon()

# launches the heartbeat worker as a daemon thread and returns the stop event
def start_heartbeat() ->threading.Event:
    stop_event = threading.Event()
    worker = threading.Thread(target=heartbeat_worker, args=(stop_event,), daemon=True)
    worker.start()
    return stop_event

# asks the daemon for the dictionary of all currently cached seen URLs
def get_seen_urls() -> dict[str, int]:
    resp = send_daemon_request({'action' : 'GET_SEEN'})
    return resp.get('cache', {}) if resp else {}

# sends newly seen URLs to the daemon to update the in-memory cache
def update_urls(new_urls: dict[str, int]) -> int:
    resp = send_daemon_request({'action' : 'UPDATE', 'cache' : new_urls})
    return resp.get('total', 0) if resp is not None and resp.get('status', 'ERROR') == 'ok' else 0
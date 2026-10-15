import os
import time
import json
import socket

HOST = '127.0.0.1' # local host
PORT = 5555 # gateway for the daemon
PING_TIME = 30
INACTIVITY_TIMEOUT = 600 # secs
PRUNING_FREQUENCY = 24 * 3600
TTL_SECONDS = 3 * 24 * 3600 # 3 days
TEMP_EXT = '.tmp'
CONNECTION_RETRIES = 10
SAVE_FREQUENCY = 10
BUFFER_SIZE = 2**19 # 1 MB

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRIPT_DIR, '..', 'json', 'seen_url.json')

def main():
    print('Booting Daemon..')
    cache = load_cache()
    deleted_count = prune_cache(cache)

    if deleted_count > 0:
        save_cache(cache)

    run_daemon(cache)
    input('Press Enter to exit...')
    

def load_cache(file_path: str = CACHE_FILE) -> dict[str, int]:
    if not os.path.exists(file_path):
        return {}

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_cache(cache: dict[str, int],  file_path: str = CACHE_FILE):
    dirs = os.path.dirname(file_path)
    if dirs:
        os.makedirs(dirs, exist_ok=True)

    temp_file = file_path + TEMP_EXT
    with open(temp_file, 'w', encoding='utf-8') as file:
        json.dump(cache, file, indent=2)

    os.replace(temp_file, file_path)

def prune_cache(cache: dict[str, int]) -> int:
    present = time.time()
    expired_url = [url for url, ts in cache.items() if (present - ts) >= TTL_SECONDS]
    for url in expired_url:
        del cache[url]

    return len(expired_url)

def handle_client(conn: socket.socket, cache: dict[str, int]) -> bool:
    response = {'status' : 'ok'}
    dirty = False
    with conn:
        # try to get raw bytes and parse it into json
        try:
            raw_bytes = conn.recv(BUFFER_SIZE) # set the buffer size
            if not raw_bytes:
                return False
            text = raw_bytes.decode('utf-8') # parse it into string
            request = json.loads(text) # parse the string to json
        except Exception:
            request = {'action' : 'INVALID'}

        raw_action = request.get('action', 'INAVLID')
        action = raw_action.strip() if isinstance(raw_action, str) else 'INVALID'
        now = time.time()
        match action:
            case 'GET_SEEN':
                response['cache'] = cache
                print(f"<-> [{now}] [GET_SEEN] Served {len(cache)} cached URLs.", flush=True)
            case 'UPDATE':
                new_urls = request.get('cache', {})
                cache.update(new_urls)
                dirty = True
                response['total']  = len(cache)
                print(f"<-> [{now}] [UPDATE] Added {len(new_urls)} new URLs (Total: {len(cache)}).", flush=True)
            case 'PING':
                response['status'] = 'ok'
                print(f"<-> [{now}] [PING] Client is active...", flush=True)
            case _:
                response['status'] = f'ERROR, could not process request: {action}.'
                print(f"<-> [{now}] [ERROR] Unknown action: {action}", flush=True)

        # dict->json string->bytes
        response_bytes = json.dumps(response).encode('utf-8')
        try:
            conn.sendall(response_bytes) # send response
        except Exception:
            pass
        return dirty

def run_daemon(cache: dict[str, int]):
    # -------------------------------------------------------------------------
    # SOCKET IPC SUMMARY (Inter-Process Communication):
    # A socket is like a fast, two-way memory pipe between two processes:
    # 1. socket()    -> create the phone/endpoint (IPv4 + TCP stream)
    # 2. setsockopt()-> allow quick restart without "Address already in use" errors
    # 3. bind()      -> register our address & port (127.0.0.1:5555)
    # 4. listen()    -> wait for incoming connections (queue backlog of 5)
    # 5. accept()    -> answer an incoming call from bbc_ai_agent.py
    # -------------------------------------------------------------------------
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))  # configuring the socket for local host and port
    server_socket.listen(5)  # setting the queue limit for how many connections can wait
    print(f'<-> TTL Daemon started. Active cached URLs: {len(cache)}')
    print(f"<-> Daemon listening on {HOST}:{PORT}...")
    
    # set timeout clock to get out of accept()
    server_socket.settimeout(PING_TIME)
    last_contact = time.time() # to save the last contact time
    last_pruned = time.time()
    dirty = False # to check if the steam got any data
    error_count = 0
    request_processed = 0
    # begin process
    try:
        while(True):
            # try to listen to the line, settimeout will eventually throw an exception
            # but if other errors causes network malfunction then do a retry for conn
            try:
                conn, addr = server_socket.accept()
                error_count = 0 # reset error count
            except socket.timeout:
                idle_time = time.time() - last_contact
                if idle_time > INACTIVITY_TIMEOUT:
                    print(f'<-> Client inactive for {INACTIVITY_TIMEOUT} secs, Shutting down...')
                    break
                continue
            except Exception as e:
                # check for network failures
                error_count += 1
                print(f'<-> Socket error encountered ({error_count}/{CONNECTION_RETRIES}) : {e}')
                if error_count >= CONNECTION_RETRIES:
                    print('💀 Network unresponsive, killing Daemon.')
                    break

                time.sleep(1) # pause momentarily before retrying
                print(f'<-> Retrying connection to {HOST}:{PORT}...')
                continue


            
            # if the client connects then log time
            last_contact = time.time()
            if handle_client(conn, cache):
                dirty = True

            request_processed += 1

            if dirty and request_processed % SAVE_FREQUENCY == 0:
                save_cache(cache)
                dirty = False

            if time.time() - last_pruned >= PRUNING_FREQUENCY:
                print('<-> Scheduled pruning underway...')
                prune_cache(cache)
                save_cache(cache)
                last_pruned = time.time()
                dirty = False
                print('<-> Cache pruned succesfully.')


    except KeyboardInterrupt:
        print('💀 Daemon manual kill override triggered.')
    finally:
        server_socket.close()
        if dirty:
            prune_cache(cache)
            save_cache(cache)   
        print('<-> Daemon shutdown complete.')
        print(f'⚙️ Total requests processed: {request_processed}')


if __name__ == '__main__':
    main()






    
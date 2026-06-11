"""
Load test: spawns N bot clients that register, login, and measure RTT.
Run from project root: python tools/load_test.py [--clients N]
"""
import socket
import threading
import json
import time
import argparse

HOST = "127.0.0.1"
PORT = 5000


def _send(sock, data: dict):
    sock.sendall((json.dumps(data) + "\n").encode("utf-8"))


def _recv_until(sock, msg_type: str, timeout: float = 5.0) -> dict | None:
    sock.settimeout(timeout)
    buf = ""
    try:
        while True:
            chunk = sock.recv(4096).decode("utf-8", errors="ignore")
            if not chunk:
                return None
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                pkt = json.loads(line)
                if pkt.get("type") == msg_type:
                    return pkt
    except Exception:
        return None


def bot_client(bot_id: int, results: list):
    username = f"loadbot_{bot_id}"
    password = "testpass123"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    start = time.time()
    try:
        sock.connect((HOST, PORT))

        # Register (ignore error if already exists)
        _send(sock, {"type": "register", "username": username, "password": password})
        _recv_until(sock, "register_ok", timeout=3)

        # Login
        _send(sock, {"type": "login", "username": username, "password": password})
        resp = _recv_until(sock, "login_ok", timeout=5)
        if not resp:
            results.append({"bot": bot_id, "ok": False, "error": "login_ok not received"})
            return

        login_latency = (time.time() - start) * 1000

        # Ping/pong RTT
        t0 = time.time()
        _send(sock, {"type": "ping", "t": t0})
        pong = _recv_until(sock, "pong", timeout=5)
        rtt_ms = (time.time() - t0) * 1000 if pong else -1

        results.append({
            "bot":           bot_id,
            "ok":            True,
            "login_ms":      round(login_latency, 1),
            "rtt_ms":        round(rtt_ms, 1),
        })
    except Exception as e:
        results.append({"bot": bot_id, "ok": False, "error": str(e)})
    finally:
        sock.close()


def run_load_test(n_clients: int):
    print(f"Launching {n_clients} bot clients against {HOST}:{PORT} ...")
    results = []
    threads = [
        threading.Thread(target=bot_client, args=(i, results), daemon=True)
        for i in range(n_clients)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    ok     = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    avg_login = sum(r["login_ms"] for r in ok) / len(ok) if ok else 0
    avg_rtt   = sum(r["rtt_ms"]   for r in ok if r["rtt_ms"] >= 0) / max(len(ok), 1)

    print(f"\n{'='*50}")
    print(f"Results: {len(ok)}/{n_clients} success | {len(failed)} failed")
    print(f"Avg login latency : {avg_login:.1f} ms")
    print(f"Avg ping RTT      : {avg_rtt:.1f} ms")
    if failed:
        print("\nFailed bots:")
        for r in failed:
            print(f"  bot_{r['bot']}: {r.get('error')}")
    print(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Werewolf load test")
    parser.add_argument("--clients", type=int, default=10, help="Number of bot clients")
    args = parser.parse_args()
    run_load_test(args.clients)

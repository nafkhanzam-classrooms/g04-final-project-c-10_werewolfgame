import socket
import json
import threading
import sys
import time

def listen_for_packets(sock):
    """Listens for packets from the server and prints them."""
    buffer = ""
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                print("\n[DISCONNECTED] Server closed the connection.")
                break
            
            buffer += data.decode("utf-8", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.strip():
                    try:
                        packet = json.loads(line)
                        print(f"\n[RECEIVE] {json.dumps(packet, indent=2)}")
                        print("Send packet (JSON format) > ", end="", flush=True)
                    except json.JSONDecodeError:
                        print(f"\n[RECEIVE RAW] {line}")
        except Exception as e:
            # Silence expected errors on close
            if "[Errno 9]" not in str(e):
                print(f"\n[ERROR] Listener error: {e}")
            break

def main():
    host = "143.198.217.44"
    port = 5000

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        print(f"[CONNECTED] Connected to {host}:{port}")
    except ConnectionRefusedError:
        print(f"[ERROR] Could not connect to server at {host}:{port}. Is server.py running?")
        return

    # Start listener thread
    threading.Thread(target=listen_for_packets, args=(sock,), daemon=True).start()

    print("\n--- Manual Packet Tester ---")
    print("Type your packet as a JSON string.")
    print("Type 'exit' to quit.")
    print("Common packet types: login, register, create, join, chat, ready, start, rooms, ping")
    
    # Examples
    print("\nQuick templates (copy-paste):")
    print('{"type": "register", "username": "alice", "password": "123"}')
    print('{"type": "login", "username": "alice", "password": "123"}')
    print('{"type": "create", "room": "TESTROOM"}')
    print('{"type": "join", "room": "TESTROOM"}')
    print('{"type": "chat", "msg": "Hello world!"}')
    print("-" * 30 + "\n")

    while True:
        try:
            user_input = input("Send packet (JSON format) > ").strip()
            
            if user_input.lower() == 'exit':
                break
            
            if not user_input:
                continue

            # Try to parse to validate JSON before sending
            try:
                packet_data = json.loads(user_input)
                # Ensure it's a dict
                if not isinstance(packet_data, dict):
                    print("[ERROR] Packet must be a JSON object (dict).")
                    continue
                
                # Send encoded packet + newline
                encoded_packet = (json.dumps(packet_data) + "\n").encode("utf-8")
                sock.sendall(encoded_packet)
                print(f"[SENT] {user_input}")
            except json.JSONDecodeError as e:
                print(f"[ERROR] Invalid JSON: {e}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}")
            break

    sock.close()
    print("\n[CLOSED] Connection closed.")

if __name__ == "__main__":
    main()

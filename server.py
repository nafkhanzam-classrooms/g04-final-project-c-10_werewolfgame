import socket
import threading
import json
import os
import time
from datetime import datetime

from game.player import Player
from game.room_manager import RoomManager
from game.packet_handler import PacketHandler, Phase
from utils.serializer import decode

HOST = "0.0.0.0"
PORT = 5000

class Server:
    def __init__(self):
        self.players = {}       # username -> Player
        self.pending = {}       # addr -> Player (before login)
        self.room_manager = RoomManager()
        self.packet_handler = PacketHandler(self)
        self._setup_logs()
        self._phase_timers = {}  # room_name -> timer thread

    def _setup_logs(self):
        os.makedirs("logs", exist_ok=True)
        self.log_file = open(f"logs/server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", "a")

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def start(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, PORT))
        server_sock.listen(10)
        self.log(f"[SERVER] Werewolf Server running on {HOST}:{PORT}")
        print(f"  TCP Server ready. Waiting for players...")

        while True:
            try:
                conn, addr = server_sock.accept()
                player = Player("", conn, addr)
                self.pending[addr] = player
                t = threading.Thread(target=self.handle_client, args=(player,), daemon=True)
                t.start()
                self.log(f"[CONNECT] New connection from {addr}")
            except Exception as e:
                self.log(f"[ERROR] Accept failed: {e}")

    def handle_client(self, player):
        buffer = ""
        try:
            while True:
                data = player.conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        packet = decode(line)
                        self.packet_handler.handle(player, packet)
                    except json.JSONDecodeError:
                        self.packet_handler.send(player, {"type": "error", "msg": "Invalid JSON"})
        except Exception as e:
            pass
        finally:
            self._on_disconnect(player)

    def _on_disconnect(self, player):
        player.connected = False
        username = player.username
        addr = player.addr

        if addr in self.pending:
            del self.pending[addr]

        if username:
            self.log(f"[DISCONNECT] {username} disconnected")
            # Keep player state for reconnect
            if username in self.players:
                self.players[username].connected = False
                # Notify room
                if player.room:
                    room = self.room_manager.get_room(player.room)
                    if room:
                        self.packet_handler.broadcast(room, {
                            "type": "system",
                            "msg": f"{username} disconnected. They can reconnect."
                        }, exclude=username)
        try:
            player.conn.close()
        except Exception:
            pass

    def start_phase_timer(self, room, duration, next_fn):
        """Start a countdown timer, then call next_fn(room)"""
        room_name = room.name

        def timer_thread():
            time.sleep(duration)
            # Check room still exists and phase hasn't changed elsewhere
            r = self.room_manager.get_room(room_name)
            if r and r.started:
                next_fn(r)

        t = threading.Thread(target=timer_thread, daemon=True)
        t.start()
        self._phase_timers[room_name] = t


if __name__ == "__main__":
    srv = Server()
    srv.start()
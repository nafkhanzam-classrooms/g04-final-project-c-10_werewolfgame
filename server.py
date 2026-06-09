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
        self.active_conns = {}  # addr -> Player (current mapped player for this socket)
        self.room_manager = RoomManager()
        self.packet_handler = PacketHandler(self)
        self._setup_logs()

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
                self.active_conns[addr] = player
                t = threading.Thread(target=self.handle_client, args=(player.addr,), daemon=True)
                t.start()
                self.log(f"[CONNECT] New connection from {addr}")
            except Exception as e:
                self.log(f"[ERROR] Accept failed: {e}")

    def handle_client(self, addr):
        buffer = ""
        try:
            while True:
                # Always use the latest player object mapped to this address
                player = self.active_conns.get(addr)
                if not player: break

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
                        # Re-fetch player in case it was swapped by a concurrent on_login/on_identify
                        player = self.active_conns.get(addr)
                        self.packet_handler.handle(player, packet)
                    except json.JSONDecodeError:
                        self.packet_handler.send(player, {"type": "error", "msg": "Invalid JSON"})
        except Exception:
            pass
        finally:
            self._on_disconnect(addr)

    def _on_disconnect(self, addr):
        player = self.active_conns.get(addr)
        if not player: return

        player.connected = False
        rname = player.room
        
        # Determine if we should PURGE or PRESERVE
        purge = True
        if rname:
            room = self.room_manager.get_room(rname)
            # If match is active, DO NOT PURGE, just mark offline
            if room and room.started:
                purge = False
                self.log(f"[OFFLINE] {player.username} disconnected from active match in {rname}")
                self.packet_handler.broadcast(room, {"type": "system", "msg": f"{player.username} disconnected!"})
                self.packet_handler._broadcast_players(room)

        if purge:
            self.log(f"[DISCONNECT] {player.username if player.username else 'Guest'} disconnected (lobby/inactive)")
            self.packet_handler.on_leave(player, {})
            if addr in self.active_conns:
                del self.active_conns[addr]
                
        try:
            player.conn.close()
        except Exception:
            pass

if __name__ == "__main__":
    srv = Server()
    srv.start()

import socket
import threading
import json
import os
import time
from datetime import datetime

from game.player import Player
from game.room_manager import RoomManager
from game.packet_handler import PacketHandler
from utils.serializer import decode
from server.database import init_db, clear_session

HOST = "0.0.0.0"
PORT = 5000

PING_WATCHDOG_INTERVAL = 5    # seconds between watchdog checks
PING_TIMEOUT           = 35   # warn if no ping received for this many seconds
OFFLINE_TIMEOUT        = 300  # purge player data after 5 minutes of offline
MAX_LINE_BYTES         = 64 * 1024   # cap a single packet line at 64KB


class Server:
    def __init__(self):
        self.active_conns  = {}   # addr -> Player
        self.room_manager  = RoomManager()
        self.packet_handler = PacketHandler(self)
        self._setup_logs()
        init_db()

    def _setup_logs(self):
        os.makedirs("logs", exist_ok=True)
        self.log_file = open(
            f"logs/server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", "a"
        )

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
        server_sock.listen(15)
        self.log(f"[SERVER] Werewolf Server running on {HOST}:{PORT}")
        print("  TCP Server ready. Waiting for players...")

        threading.Thread(target=self._watchdog_loop, daemon=True).start()

        while True:
            try:
                conn, addr = server_sock.accept()
                player = Player("", conn, addr)
                player.last_ping = time.time()
                self.active_conns[addr] = player
                t = threading.Thread(target=self.handle_client, args=(addr,), daemon=True)
                t.start()
                self.log(f"[CONNECT] New connection from {addr}")
            except Exception as e:
                self.log(f"[ERROR] Accept failed: {e}")

    def handle_client(self, addr):
        buffer = ""
        try:
            while True:
                player = self.active_conns.get(addr)
                if not player:
                    break
                data = player.conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")

                # Process all complete lines in the buffer first.
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    # Reject oversized single lines.
                    if len(line) > MAX_LINE_BYTES:
                        self.log(f"[INVALID] {addr} oversized line ({len(line)}B), dropped")
                        p = self.active_conns.get(addr)
                        if p:
                            self.packet_handler.send(
                                p, {"type": "error", "msg": "Packet too large"}
                            )
                        continue

                    try:
                        packet = decode(line)
                    except json.JSONDecodeError:
                        self.log(f"[INVALID] {addr} malformed JSON: {line[:120]!r}")
                        p = self.active_conns.get(addr)
                        if p:
                            self.packet_handler.send(
                                p, {"type": "error", "msg": "Malformed packet"}
                            )
                        continue

                    p = self.active_conns.get(addr)
                    if not p:
                        continue
                    # Defense-in-depth: never let a handler exception kill this thread.
                    try:
                        self.packet_handler.handle(p, packet)
                    except Exception as e:
                        self.log(f"[HANDLER_ERROR] {addr} {packet.get('type','?')}: {e}")
                        self.packet_handler.send(
                            p, {"type": "error", "msg": "Internal handler error"}
                        )

                # After draining complete lines, an oversized incomplete buffer
                # means the client is flooding without sending a newline.
                # Drop the buffer (lenient) and notify them.
                if len(buffer) > MAX_LINE_BYTES:
                    self.log(f"[INVALID] {addr} oversized buffer ({len(buffer)}B), dropped")
                    p = self.active_conns.get(addr)
                    if p:
                        self.packet_handler.send(
                            p, {"type": "error", "msg": "Payload too large; buffer cleared"}
                        )
                    buffer = ""
        except Exception:
            pass
        finally:
            self._on_disconnect(addr)

    def _on_disconnect(self, addr):
        player = self.active_conns.pop(addr, None)
        if not player:
            return

        player.connected = False
        rname = player.room

        if rname:
            room = self.room_manager.get_room(rname)
            if room and room.started:
                # Keep player slot alive for reconnect; just mark offline
                self.log(f"[OFFLINE] {player.username} disconnected from active match in {rname}")
                self.packet_handler.broadcast(
                    room,
                    {"type": "system", "msg": f"{player.username} disconnected!"},
                    exclude=player.username
                )
                self.packet_handler._broadcast_players(room)
                # Mark session as offline but keep room_code so they can reconnect
                if player.username:
                    clear_session(player.username)
                try:
                    player.conn.close()
                except Exception:
                    pass
                return

        # Lobby disconnect — full cleanup
        self.log(
            f"[DISCONNECT] {player.username if player.username else 'Guest'} disconnected"
        )
        self.packet_handler.on_leave(player, {})
        try:
            player.conn.close()
        except Exception:
            pass

    def _watchdog_loop(self):
        """Log a warning for any connected player who hasn't pinged in PING_TIMEOUT seconds.
        Also purge players who have been offline for more than OFFLINE_TIMEOUT.
        """
        while True:
            time.sleep(PING_WATCHDOG_INTERVAL)
            now = time.time()

            # 1. Check active connections (logged-in and guest)
            for player in list(self.active_conns.values()):
                if not player.connected or not player.username:
                    continue
                if player.last_ping and (now - player.last_ping) > PING_TIMEOUT:
                    self.log(
                        f"[WARN] No ping from {player.username} for "
                        f"{int(now - player.last_ping)}s"
                    )

            # 2. Purge offline players from rooms (Reconnect Timeout)
            for room in list(self.room_manager.rooms.values()):
                to_purge = []

                with room.lock:
                    for p in room.game.players.values():
                        if not p.connected and p.last_ping:
                            if (now - p.last_ping) > OFFLINE_TIMEOUT:
                                to_purge.append(p)

                for p in to_purge:
                    self.log(
                        f"[PURGE] Removing stale offline player "
                        f"{p.username} from {room.name}"
                    )
                    self.room_manager.leave_room(p)
                    self.packet_handler._broadcast_players(room)


if __name__ == "__main__":
    srv = Server()
    srv.start()

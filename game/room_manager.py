from game.game_state import GameState, Phase

MIN_PLAYERS = 4
MAX_PLAYERS = 8

class Room:
    def __init__(self, name, host):
        self.name = name
        self.host = host
        self.game = GameState(name)
        self.started = False

    def add_player(self, player, ignore_started=False):
        if len(self.game.players) >= MAX_PLAYERS:
            return False, "Room is full"
        if self.started and not ignore_started:
            return False, "Game already started"
        
        key = player.username if player.username else f"Guest_{player.addr[1]}"
        self.game.add_player(key, player)
        player.room = self.name
        return True, "OK"

    def remove_player(self, username_or_key):
        self.game.remove_player(username_or_key)

    def can_start(self):
        if len(self.game.players) < MIN_PLAYERS:
            return False
        for p in self.game.players.values():
            if not p.ready:
                return False
        return True

    def player_count(self):
        return len(self.game.players)

    def status(self):
        if self.started:
            return f"In Game ({self.game.phase.value})"
        return "Waiting"


class RoomManager:
    def __init__(self):
        self.rooms = {}

    def _generate_code(self):
        import random
        import string
        chars = string.ascii_uppercase + string.digits
        while True:
            code = ''.join(random.choice(chars) for _ in range(6))
            if code not in self.rooms:
                return code

    def create_room(self, name, host_player):
        if not name or name == "AUTO":
            name = self._generate_code()
            
        if name in self.rooms:
            return None, "Room already exists"
        
        room = Room(name, host_player.username)
        self.rooms[name] = room
        ok, msg = room.add_player(host_player)
        return room, msg

    def join_room(self, name, player, ignore_started=False):
        name = name.upper()
        if name not in self.rooms:
            return None, "Room not found"
        room = self.rooms[name]
        ok, msg = room.add_player(player, ignore_started=ignore_started)
        if not ok:
            return None, msg
        return room, "OK"

    def leave_room(self, player):
        if player.room and player.room in self.rooms:
            room = self.rooms[player.room]
            rname = player.room
            
            # Use same key logic as add_player
            key = player.username if player.username else f"Guest_{player.addr[1]}"
            room.remove_player(key)
            
            # Reset player room state completely
            player.room = None
            player.ready = False
            player.alive = True
            
            if room.player_count() == 0:
                del self.rooms[rname]
            elif room.host == player.username or room.host == "":
                # Elect new host
                if room.game.players:
                    new_host = list(room.game.players.values())[0]
                    room.host = new_host.username
                else:
                    if rname in self.rooms: del self.rooms[rname]

    def get_room(self, name):
        return self.rooms.get(name)

    def list_rooms(self):
        result = []
        for name, room in self.rooms.items():
            result.append({
                "name": name,
                "players": room.player_count(),
                "max": MAX_PLAYERS,
                "status": room.status()
            })
        return result

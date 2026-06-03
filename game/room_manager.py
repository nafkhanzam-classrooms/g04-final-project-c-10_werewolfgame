from game.game_state import GameState, Phase

MIN_PLAYERS = 2
MAX_PLAYERS = 6

class Room:
    def __init__(self, name, host):
        self.name = name
        self.host = host
        self.game = GameState(name)
        self.started = False

    def add_player(self, player):
        if len(self.game.players) >= MAX_PLAYERS:
            return False, "Room is full"
        if self.started:
            return False, "Game already started"
        self.game.add_player(player.username, player)
        player.room = self.name
        return True, "OK"

    def remove_player(self, username):
        self.game.remove_player(username)

    def can_start(self):
        return len(self.game.players) >= MIN_PLAYERS

    def player_count(self):
        return len(self.game.players)

    def status(self):
        if self.started:
            return f"In Game ({self.game.phase.value})"
        return "Waiting"


class RoomManager:
    def __init__(self):
        self.rooms = {}

    def create_room(self, name, host_player):
        if name in self.rooms:
            return None, "Room already exists"
        room = Room(name, host_player.username)
        self.rooms[name] = room
        ok, msg = room.add_player(host_player)
        return room, msg

    def join_room(self, name, player):
        if name not in self.rooms:
            return None, "Room not found"
        room = self.rooms[name]
        ok, msg = room.add_player(player)
        if not ok:
            return None, msg
        return room, "OK"

    def leave_room(self, player):
        if player.room and player.room in self.rooms:
            room = self.rooms[player.room]
            rname = player.room
            room.remove_player(player.username)
            player.room = None
            if room.player_count() == 0 and rname in self.rooms:
                del self.rooms[rname]

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
import random
from enum import Enum

class Phase(Enum):
    LOBBY = "lobby"
    NIGHT = "night"
    DAY = "day"
    VOTING = "voting"
    ENDED = "ended"

class Role(Enum):
    VILLAGER = "Villager"
    WEREWOLF = "Werewolf"
    SEER = "Seer"

class GameState:
    def __init__(self, room_name):
        self.room_name = room_name
        self.players = {}       # username -> Player object
        self.phase = Phase.LOBBY
        self.votes = {}         # voter -> target
        self.night_kills = {}   # killer -> target
        self.seer_check = {}    # seer -> target
        self.eliminated = []    # list of eliminated usernames
        self.winner = None
        self.round = 0
        self.phase_timer = 0

    def add_player(self, username, player_obj):
        self.players[username] = player_obj

    def remove_player(self, username):
        if username in self.players:
            del self.players[username]

    def assign_roles(self):
        usernames = list(self.players.keys())
        random.shuffle(usernames)
        n = len(usernames)
        num_wolves = max(1, n // 3)
        num_seers = 1 if n >= 4 else 0

        for i, uname in enumerate(usernames):
            if i < num_wolves:
                self.players[uname].role = Role.WEREWOLF
            elif i < num_wolves + num_seers:
                self.players[uname].role = Role.SEER
            else:
                self.players[uname].role = Role.VILLAGER

    def get_alive_players(self):
        return [u for u, p in self.players.items() if p.alive]

    def get_dead_players(self):
        return [u for u, p in self.players.items() if not p.alive]

    def get_werewolves(self):
        return [u for u, p in self.players.items() if p.role == Role.WEREWOLF and p.alive]

    def get_villagers(self):
        return [u for u, p in self.players.items() if p.role != Role.WEREWOLF and p.alive]

    def check_win(self):
        wolves = self.get_werewolves()
        villagers = self.get_villagers()
        if len(wolves) == 0:
            self.winner = "Villager"
            self.phase = Phase.ENDED
            return "Villager"
        if len(wolves) >= len(villagers):
            self.winner = "Werewolf"
            self.phase = Phase.ENDED
            return "Werewolf"
        return None

    def tally_votes(self):
        count = {}
        for target in self.votes.values():
            count[target] = count.get(target, 0) + 1
        if not count:
            return None
        return max(count, key=count.get)

    def reset_round(self):
        self.votes = {}
        self.night_kills = {}
        self.seer_check = {}
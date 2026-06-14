import random
from enum import Enum


class Phase(Enum):
    LOBBY  = "lobby"
    NIGHT  = "night"
    DAY    = "day"
    VOTING = "voting"
    ENDED  = "ended"


class Role(Enum):
    VILLAGER = "Villager"
    WEREWOLF = "Werewolf"
    SEER     = "Seer"
    GUARD   = "Guard"
    HUNTER   = "Hunter"


class GameState:
    def __init__(self, room_name):
        self.room_name   = room_name
        self.players     = {}   # username -> Player
        self.phase       = Phase.LOBBY
        self.votes       = {}   # voter -> target
        self.night_kills = {}   # killer -> target  (werewolf votes)
        self.night_protect = None   # guard's chosen target
        self.seer_check  = {}   # seer -> target
        self.eliminated  = []
        self.winner      = None
        self.round       = 0
        self.phase_timer = 0
        # Hunter state: username of hunter pending their shot, or None
        self.hunter_pending = None

    def add_player(self, username, player_obj):
        self.players[username] = player_obj

    def remove_player(self, username):
        if username in self.players:
            del self.players[username]

    def assign_roles(self):
        """
        PRD role scaling:
          4–5  players: 1 wolf, Seer
          6-7 players: 2 wolves, Seer, Guard
          8–10: 2 wolves, Seer, Guard, Hunter
          13–15: 3 wolves, Seer, Guard, Hunter
        """
        usernames = list(self.players.keys())
        random.shuffle(usernames)
        n = len(usernames)

        if n <= 5:
            num_wolves, specials = 1, [Role.SEER]
        elif n <= 7:
            num_wolves, specials = 2, [Role.SEER, Role.GUARD]
        elif n <= 10:
            num_wolves, specials = 2, [Role.SEER, Role.GUARD, Role.HUNTER]
        else:
            num_wolves, specials = 3, [Role.SEER, Role.GUARD, Role.HUNTER]

        idx = 0
        for i in range(num_wolves):
            self.players[usernames[idx]].role = Role.WEREWOLF
            idx += 1
        for role in specials:
            if idx < n:
                self.players[usernames[idx]].role = role
                idx += 1
        for i in range(idx, n):
            self.players[usernames[i]].role = Role.VILLAGER

    def get_alive_players(self):
        return [u for u, p in self.players.items() if p.alive]

    def get_dead_players(self):
        return [u for u, p in self.players.items() if not p.alive]

    def get_werewolves(self):
        return [u for u, p in self.players.items() if p.role == Role.WEREWOLF and p.alive]

    def get_villagers(self):
        return [u for u, p in self.players.items() if p.role != Role.WEREWOLF and p.alive]

    def check_win(self):
        wolves     = self.get_werewolves()
        villagers  = self.get_villagers()
        if len(wolves) == 0:
            self.winner = "Villager"
            self.phase  = Phase.ENDED
            return "Villager"
        if len(wolves) >= len(villagers):
            self.winner = "Werewolf"
            self.phase  = Phase.ENDED
            return "Werewolf"
        return None

    def tally_votes(self):
        """Returns the majority-vote target, or None on tie / no votes."""
        count = {}
        for target in self.votes.values():
            count[target] = count.get(target, 0) + 1
        if not count:
            return None
        top_votes = max(count.values())
        leaders   = [t for t, v in count.items() if v == top_votes]
        if len(leaders) > 1:
            return None  # tie → no elimination
        return leaders[0]

    def reset_round(self):
        self.votes         = {}
        self.night_kills   = {}
        self.night_protect = None
        self.seer_check    = {}
        self.hunter_pending = None

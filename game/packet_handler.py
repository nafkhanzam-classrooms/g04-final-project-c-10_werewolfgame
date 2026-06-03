from game.game_state import Phase, Role
from utils.serializer import encode
import time
import threading

# Phase durations in seconds
PHASE_DURATION = {
    Phase.NIGHT:  45,
    Phase.DAY:    90,
    Phase.VOTING: 45,
}

class PacketHandler:
    def __init__(self, server):
        self.server = server
        self._phase_timers = {}   

    def handle(self, player, packet):
        ptype = packet.get("type")
        handlers = {
            "login":      self.on_login,
            "create":     self.on_create,
            "join":       self.on_join,
            "leave":      self.on_leave,
            "rooms":      self.on_rooms,
            "start":      self.on_start,
            "chat":       self.on_chat,
            "kill":       self.on_kill,
            "vote":       self.on_vote,
            "check":      self.on_check,
            "ping":       self.on_ping,
            "players":    self.on_players,
        }
        fn = handlers.get(ptype)
        if fn:
            fn(player, packet)
        else:
            self.send(player, {"type": "error", "msg": f"Unknown packet type: {ptype}"})


    def send(self, player, data):
        try:
            player.conn.sendall(encode(data))
        except Exception:
            pass

    def broadcast(self, room, data, exclude=None):
        for uname, p in list(room.game.players.items()):
            if exclude and uname == exclude:
                continue
            if p.connected:
                self.send(p, data)

    def broadcast_wolves(self, room, data):
        for uname, p in list(room.game.players.items()):
            if p.role == Role.WEREWOLF and p.connected:
                self.send(p, data)

    def _broadcast_players(self, room):
        """Push updated player list to everyone in room."""
        players_info = [
            {"username": uname, "alive": p.alive, "host": uname == room.host}
            for uname, p in room.game.players.items()
        ]
        self.broadcast(room, {"type": "players_list", "players": players_info,
                               "phase": room.game.phase.value})

    def _start_phase_timer(self, room, phase, duration, callback):
        """Start a countdown timer for a phase. Cancels any existing timer."""
        rname = room.name
        cancel_ev = threading.Event()

        # cancel old timer
        if rname in self._phase_timers:
            old_ev = self._phase_timers[rname]
            old_ev.set()

        self._phase_timers[rname] = cancel_ev

        end_time = time.time() + duration

        def countdown():
            while not cancel_ev.is_set():
                remaining = int(end_time - time.time())
                if remaining <= 0:
                    break
                # broadcast timer tick every second
                r = self.server.room_manager.get_room(rname)
                if r and r.started and r.game.phase == phase:
                    self.broadcast(r, {"type": "timer", "seconds": remaining, "phase": phase.value})
                cancel_ev.wait(1.0)

            if not cancel_ev.is_set():
                r = self.server.room_manager.get_room(rname)
                if r and r.started and r.game.phase == phase:
                    callback(r)

        t = threading.Thread(target=countdown, daemon=True)
        t.start()


    def on_login(self, player, packet):
        username = packet.get("username", "").strip()
        if not username or len(username) < 2:
            self.send(player, {"type": "error", "msg": "Invalid username"})
            return
        if username in self.server.players:
            old = self.server.players[username]
            old.conn = player.conn
            old.connected = True
            self.server.players[username] = old
            self.send(old, {"type": "login_ok", "username": username, "reconnect": True})
            self.server.log(f"[RECONNECT] {username}")
            if old.room:
                room = self.server.room_manager.get_room(old.room)
                if room:
                    players_info = [
                        {"username": u, "alive": p.alive, "host": u == room.host}
                        for u, p in room.game.players.items()
                    ]
                    self.send(old, {"type": "rejoin", "room": old.room,
                                    "phase": room.game.phase.value,
                                    "round": room.game.round,
                                    "players": players_info})
        else:
            player.username = username
            self.server.players[username] = player
            self.send(player, {"type": "login_ok", "username": username, "reconnect": False})
            self.server.log(f"[LOGIN] {username}")

    def on_create(self, player, packet):
        name = packet.get("room", "").strip()
        if not name:
            self.send(player, {"type": "error", "msg": "Invalid room name"})
            return
        room, msg = self.server.room_manager.create_room(name, player)
        if room is None:
            self.send(player, {"type": "error", "msg": msg})
            return
        self._send_room_joined(player, room, host=True)
        self.server.log(f"[CREATE] {player.username} created room '{name}'")

    def on_join(self, player, packet):
        name = packet.get("room", "").strip()
        room, msg = self.server.room_manager.join_room(name, player)
        if room is None:
            self.send(player, {"type": "error", "msg": msg})
            return
        self._send_room_joined(player, room, host=False)
        self.broadcast(room, {"type": "system", "msg": f"{player.username} joined the room."},
                       exclude=player.username)
        self._broadcast_players(room)
        self.server.log(f"[JOIN] {player.username} joined room '{name}'")

    def _send_room_joined(self, player, room, host):
        players_info = [
            {"username": u, "alive": p.alive, "host": u == room.host}
            for u, p in room.game.players.items()
        ]
        self.send(player, {"type": "room_joined", "room": room.name, "host": host,
                            "players": players_info})

    def on_leave(self, player, packet):
        if not player.room:
            self.send(player, {"type": "error", "msg": "Not in a room"})
            return
        room = self.server.room_manager.get_room(player.room)
        rname = player.room
        self.server.room_manager.leave_room(player)
        if room:
            self.broadcast(room, {"type": "system", "msg": f"{player.username} left the room."})
            self._broadcast_players(room)
        self.send(player, {"type": "left_room", "room": rname})
        self.server.log(f"[LEAVE] {player.username} left room '{rname}'")

    def on_rooms(self, player, packet):
        rooms = self.server.room_manager.list_rooms()
        self.send(player, {"type": "rooms_list", "rooms": rooms})

    def on_start(self, player, packet):
        if not player.room:
            self.send(player, {"type": "error", "msg": "Not in a room"})
            return
        room = self.server.room_manager.get_room(player.room)
        if room.host != player.username:
            self.send(player, {"type": "error", "msg": "Only host can start"})
            return
        if not room.can_start():
            self.send(player, {"type": "error", "msg": "Need at least 2 players"})
            return
        if room.started:
            self.send(player, {"type": "error", "msg": "Already started"})
            return

        room.started = True
        room.game.assign_roles()
        room.game.phase = Phase.NIGHT
        room.game.round = 1

        # send private roles
        for uname, p in room.game.players.items():
            self.send(p, {"type": "role_assigned", "role": p.role.value,
                           "team": "werewolf" if p.role == Role.WEREWOLF else "good"})

        wolves = room.game.get_werewolves()
        for uname in wolves:
            self.send(room.game.players[uname], {"type": "wolf_team", "wolves": wolves})

        alive = room.game.get_alive_players()
        duration = PHASE_DURATION[Phase.NIGHT]
        self.broadcast(room, {"type": "phase_change", "phase": "night", "round": 1,
                               "duration": duration, "alive": alive,
                               "msg": "Night falls. Werewolves, choose your victim."})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.NIGHT, duration, self._night_timeout)
        self.server.log(f"[START] Game started in room '{room.name}'")

    def on_chat(self, player, packet):
        if not player.room:
            self.send(player, {"type": "error", "msg": "Not in a room"})
            return
        room = self.server.room_manager.get_room(player.room)
        msg = packet.get("msg", "").strip()
        if not msg:
            return
        if not player.alive:
            for uname, p in room.game.players.items():
                if not p.alive and p.connected:
                    self.send(p, {"type": "chat", "sender": player.username, "msg": msg, "dead": True})
            return
        if room.game.phase == Phase.NIGHT:
            if player.role == Role.WEREWOLF:
                self.broadcast_wolves(room, {"type": "chat", "sender": player.username,
                                              "msg": msg, "wolf_chat": True})
            else:
                self.send(player, {"type": "error", "msg": "You cannot talk at night"})
        else:
            self.broadcast(room, {"type": "chat", "sender": player.username, "msg": msg})

    def on_kill(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT:
            self.send(player, {"type": "error", "msg": "Can only kill at night"})
            return
        if player.role != Role.WEREWOLF:
            self.send(player, {"type": "error", "msg": "Only werewolves can kill"})
            return
        if not player.alive:
            self.send(player, {"type": "error", "msg": "Dead players cannot act"})
            return
        target = packet.get("target", "").strip()
        if target not in room.game.players:
            self.send(player, {"type": "error", "msg": "Player not found"})
            return
        if target == player.username:
            self.send(player, {"type": "error", "msg": "Cannot kill yourself"})
            return
        if not room.game.players[target].alive:
            self.send(player, {"type": "error", "msg": "Target is already dead"})
            return
        if room.game.players[target].role == Role.WEREWOLF:
            self.send(player, {"type": "error", "msg": "Cannot kill your teammate"})
            return

        room.game.night_kills[player.username] = target
        self.send(player, {"type": "kill_confirm", "target": target,
                            "msg": f"You marked {target} for death."})
        self.broadcast_wolves(room, {"type": "wolf_action",
                                      "msg": f"{player.username} targeted {target}"})
        wolves = room.game.get_werewolves()
        if all(w in room.game.night_kills for w in wolves):
            self._night_timeout(room)

    def on_check(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT:
            self.send(player, {"type": "error", "msg": "Can only check at night"})
            return
        if player.role != Role.SEER:
            self.send(player, {"type": "error", "msg": "You are not the Seer"})
            return
        if not player.alive:
            self.send(player, {"type": "error", "msg": "Dead players cannot act"})
            return
        target = packet.get("target", "").strip()
        if target not in room.game.players:
            self.send(player, {"type": "error", "msg": "Player not found"})
            return
        target_player = room.game.players[target]
        is_wolf = target_player.role == Role.WEREWOLF
        self.send(player, {"type": "seer_result", "target": target, "is_werewolf": is_wolf,
                            "msg": f"{target} is {'a WEREWOLF' if is_wolf else 'NOT a werewolf'}."})

    def on_vote(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.VOTING:
            self.send(player, {"type": "error", "msg": "Not voting phase"})
            return
        if not player.alive:
            self.send(player, {"type": "error", "msg": "Dead players cannot vote"})
            return
        if player.username in room.game.votes:
            self.send(player, {"type": "error", "msg": "Already voted"})
            return
        target = packet.get("target", "").strip()
        if target not in room.game.players:
            self.send(player, {"type": "error", "msg": "Player not found"})
            return
        if not room.game.players[target].alive:
            self.send(player, {"type": "error", "msg": "Target is already dead"})
            return
        room.game.votes[player.username] = target
        self.broadcast(room, {"type": "vote_cast", "voter": player.username, "target": target,
                               "votes": dict(room.game.votes)})
        self.server.log(f"[VOTE] {player.username} voted for {target}")
        alive = room.game.get_alive_players()
        if len(room.game.votes) >= len(alive):
            self._resolve_vote(room)

    def on_ping(self, player, packet):
        self.send(player, {"type": "pong", "t": packet.get("t", 0)})

    def on_players(self, player, packet):
        if not player.room:
            self.send(player, {"type": "error", "msg": "Not in a room"})
            return
        room = self.server.room_manager.get_room(player.room)
        players_info = [
            {"username": u, "alive": p.alive, "host": u == room.host}
            for u, p in room.game.players.items()
        ]
        self.send(player, {"type": "players_list", "players": players_info,
                            "phase": room.game.phase.value})


    def _night_timeout(self, room):
        if room.game.phase != Phase.NIGHT:
            return
        kills = room.game.night_kills
        if kills:
            from collections import Counter
            victim = Counter(kills.values()).most_common(1)[0][0]
            room.game.players[victim].alive = False
            room.game.eliminated.append(victim)
            self.server.log(f"[KILL] {victim} killed in '{room.name}'")
            winner = room.game.check_win()
            if winner:
                self._announce_winner(room, winner)
                return
            self._advance_to_day(room, victim)
        else:
            self._advance_to_day(room, None)

    def _advance_to_day(self, room, victim):
        if room.game.phase != Phase.NIGHT:
            return
        room.game.phase = Phase.DAY
        alive = room.game.get_alive_players()
        duration = PHASE_DURATION[Phase.DAY]
        msg = f"{victim} was killed last night." if victim else "No one was killed last night."
        self.broadcast(room, {"type": "phase_change", "phase": "day",
                               "round": room.game.round, "duration": duration,
                               "alive": alive, "msg": msg, "dead": victim})
        self._broadcast_players(room)
        self.server.log(f"[PHASE] Day {room.game.round} in '{room.name}'")
        self._start_phase_timer(room, Phase.DAY, duration,
                                 lambda r: self._advance_to_voting(r))

    def _advance_to_voting(self, room):
        if room.game.phase != Phase.DAY:
            return
        room.game.phase = Phase.VOTING
        room.game.votes = {}
        alive = room.game.get_alive_players()
        duration = PHASE_DURATION[Phase.VOTING]
        self.broadcast(room, {"type": "phase_change", "phase": "voting",
                               "round": room.game.round, "duration": duration,
                               "alive": alive,
                               "msg": "Voting phase! Click a player to vote them out."})
        self._broadcast_players(room)
        self.server.log(f"[PHASE] Voting in '{room.name}'")
        self._start_phase_timer(room, Phase.VOTING, duration,
                                 lambda r: self._resolve_vote(r))

    def _resolve_vote(self, room):
        if room.game.phase != Phase.VOTING:
            return
        # cancel timer
        rname = room.name
        if rname in self._phase_timers:
            self._phase_timers[rname].set()

        eliminated = room.game.tally_votes()
        if eliminated:
            room.game.players[eliminated].alive = False
            room.game.eliminated.append(eliminated)
            role_reveal = room.game.players[eliminated].role.value
            self.broadcast(room, {"type": "eliminated", "player": eliminated,
                                   "role": role_reveal,
                                   "msg": f"{eliminated} was eliminated! They were a {role_reveal}."})
            self.server.log(f"[ELIMINATE] {eliminated} in '{room.name}'")
        else:
            self.broadcast(room, {"type": "system", "msg": "No majority. Nobody eliminated."})

        winner = room.game.check_win()
        if winner:
            self._announce_winner(room, winner)
            return

        room.game.round += 1
        room.game.reset_round()
        room.game.phase = Phase.NIGHT
        alive = room.game.get_alive_players()
        duration = PHASE_DURATION[Phase.NIGHT]
        self.broadcast(room, {"type": "phase_change", "phase": "night",
                               "round": room.game.round, "duration": duration,
                               "alive": alive,
                               "msg": "Night falls again. Werewolves, choose your next victim."})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.NIGHT, duration, self._night_timeout)

    def _announce_winner(self, room, winner):
        roles = {u: p.role.value for u, p in room.game.players.items()}
        self.broadcast(room, {"type": "game_over", "winner": winner, "roles": roles,
                               "msg": f"Game Over! {winner}s WIN!"})
        room.started = False
        room.game.phase = Phase.ENDED
        # cancel timer
        rname = room.name
        if rname in self._phase_timers:
            self._phase_timers[rname].set()
        self.server.log(f"[WIN] {winner} wins in room '{room.name}'")
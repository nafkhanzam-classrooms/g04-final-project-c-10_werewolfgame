import time
import threading

from game.game_state import Phase, Role
from game.room_manager import MIN_PLAYERS
from utils.serializer import encode
from server.database import (
    register_user, login_user, is_online,
    set_connected, get_session, clear_session
)

PHASE_DURATION = {
    Phase.NIGHT:  45,
    Phase.DAY:    90,
    Phase.VOTING: 45,
}

HUNTER_SHOT_TIMEOUT = 20   # seconds


class PacketHandler:
    def __init__(self, server):
        self.server        = server
        self._phase_timers = {}   # room_name -> threading.Event (cancel)

    # ------------------------------------------------------------------ #
    #  Dispatch                                                          #
    # ------------------------------------------------------------------ #

    def handle(self, player, packet):
        ptype = packet.get("type")
        handlers = {
            "register": self.on_register,
            "login":    self.on_login,
            "create":   self.on_create,
            "join":     self.on_join,
            "leave":    self.on_leave,
            "rooms":    self.on_rooms,
            "start":    self.on_start,
            "ready":    self.on_ready,
            "chat":     self.on_chat,
            "kill":     self.on_kill,
            "protect":  self.on_protect,
            "check":    self.on_check,
            "vote":     self.on_vote,
            "hunter_shot": self.on_hunter_shot,
            "ping":     self.on_ping,
            "players":  self.on_players,
        }
        fn = handlers.get(ptype)
        if fn:
            fn(player, packet)
        else:
            self.send(player, {"type": "error", "msg": f"Unknown packet type: {ptype}"})

    # ------------------------------------------------------------------ #
    #  Send helpers                                                        #
    # ------------------------------------------------------------------ #

    def send(self, player, data):
        try:
            player.conn.sendall(encode(data))
        except Exception:
            pass

    def broadcast(self, room, data, exclude=None):
        for p in list(room.game.players.values()):
            if exclude and p.username == exclude:
                continue
            if p.connected:
                self.send(p, data)

    def broadcast_wolves(self, room, data):
        for p in list(room.game.players.values()):
            if p.role == Role.WEREWOLF and p.connected:
                self.send(p, data)

    def _broadcast_players(self, room):
        players_info = [
            {
                "username":  p.username,
                "alive":     p.alive,
                "ready":     p.ready,
                "connected": p.connected,
                "host":      p.username == room.host,
            }
            for p in room.game.players.values()
        ]
        self.broadcast(room, {"type": "players_list", "players": players_info,
                               "phase": room.game.phase.value})

    # ------------------------------------------------------------------ #
    #  Auth handlers                                                       #
    # ------------------------------------------------------------------ #

    def on_register(self, player, packet):
        username = packet.get("username", "").strip()
        password = packet.get("password", "").strip()
        if len(username) < 2:
            self.send(player, {"type": "error", "msg": "Username must be at least 2 characters"})
            return
        if len(password) < 4:
            self.send(player, {"type": "error", "msg": "Password must be at least 4 characters"})
            return
        ok, msg = register_user(username, password)
        if not ok:
            self.send(player, {"type": "error", "msg": msg})
            return
        self.send(player, {"type": "register_ok", "username": username})

    def on_login(self, player, packet):
        username = packet.get("username", "").strip()
        password = packet.get("password", "").strip()

        ok, msg = login_user(username, password)
        if not ok:
            self.send(player, {"type": "error", "msg": msg})
            return

        # Check for a live reconnectable session BEFORE the duplicate-login guard.
        # A player mid-game whose socket dropped is marked offline by _on_disconnect;
        # we must let them back in even though their DB row may still say is_connected=1
        # (race between clear_session and a fast reconnect).
        session = get_session(username)
        room_for_reconnect = None
        if session and session["room_code"]:
            room = self.server.room_manager.get_room(session["room_code"])
            if room and room.started and username in room.game.players:
                old_p = room.game.players[username]
                if not old_p.connected:
                    room_for_reconnect = room

        # Duplicate login guard — only block if no valid reconnect slot exists
        if room_for_reconnect is None and is_online(username):
            self.send(player, {"type": "error", "msg": "Already logged in from another session"})
            return

        player.username = username

        if room_for_reconnect is not None:
            room  = room_for_reconnect
            old_p = room.game.players[username]
            old_p.conn      = player.conn
            old_p.connected = True
            old_p.last_ping = time.time()
            self.server.active_conns[player.addr] = old_p
            set_connected(username, room.name, True)

            self.send(old_p, {"type": "login_ok", "username": username, "reconnect": True})
            self._send_state_snapshot(old_p, room)
            self._broadcast_players(room)
            self.broadcast(room, {"type": "system", "msg": f"{username} reconnected!"}, exclude=username)
            self.server.log(f"[RECONNECT] {username} room={room.name}")
            return

        # Fresh login — clear any stale session leftover from a previous crashed connection
        clear_session(username)
        set_connected(username, "", True)
        self.send(player, {"type": "login_ok", "username": username, "reconnect": False})
        self.server.log(f"[LOGIN] {username} OK")

    def _send_state_snapshot(self, player, room):
        players_info = [
            {
                "username":  p.username,
                "alive":     p.alive,
                "connected": p.connected,
            }
            for p in room.game.players.values()
        ]
        self.send(player, {
            "type":          "state_snapshot",
            "phase":         room.game.phase.value,
            "time_remaining": room.game.phase_timer,
            "role":          player.role.value,
            "players":       players_info,
        })

    # ------------------------------------------------------------------ #
    #  Room handlers                                                       #
    # ------------------------------------------------------------------ #

    def on_create(self, player, packet):
        if not player.username:
            self.send(player, {"type": "error", "msg": "Login required"})
            return
        if player.room:
            self.on_leave(player, {})
        name = packet.get("room", "").strip().upper()
        room, msg = self.server.room_manager.create_room(name, player)
        if room is None:
            self.send(player, {"type": "error", "msg": msg})
            return
        self._send_room_joined(player, room, host=True)

    def on_join(self, player, packet):
        if not player.username:
            self.send(player, {"type": "error", "msg": "Login required"})
            return
        name = packet.get("room", "").strip().upper()
        room = self.server.room_manager.get_room(name)

        # Reconnect path: game is active and this username already has a slot
        if room and room.started and player.username in room.game.players:
            old_p = room.game.players[player.username]
            old_p.conn      = player.conn
            old_p.connected = True
            old_p.last_ping = time.time()
            self.server.active_conns[player.addr] = old_p
            set_connected(old_p.username, room.name, True)

            self.send(old_p, {"type": "login_ok", "username": old_p.username, "reconnect": True})
            self._send_state_snapshot(old_p, room)
            self._broadcast_players(room)
            self.broadcast(room, {"type": "system", "msg": f"{old_p.username} reconnected!"},
                           exclude=old_p.username)
            self.server.log(f"[RECONNECT] {old_p.username} via join room={room.name}")
            return

        if player.room:
            self.on_leave(player, {})
        room, msg = self.server.room_manager.join_room(name, player)
        if room is None:
            self.send(player, {"type": "error", "msg": msg})
            return
        self._send_room_joined(player, room, host=(room.host == player.username))
        self._broadcast_players(room)

    def _send_room_joined(self, player, room, host):
        players_info = [
            {
                "username":  p.username,
                "alive":     p.alive,
                "ready":     p.ready,
                "connected": p.connected,
                "host":      p.username == room.host,
            }
            for p in room.game.players.values()
        ]
        self.send(player, {"type": "room_joined", "room": room.name,
                            "host": host, "players": players_info})

    def on_leave(self, player, packet):
        if not player.room:
            return
        rname = player.room
        room  = self.server.room_manager.get_room(rname)
        player.ready = False
        self.server.room_manager.leave_room(player)
        if room:
            self._broadcast_players(room)
        clear_session(player.username)
        self.send(player, {"type": "left_room", "room": rname})

    def on_rooms(self, player, packet):
        self.send(player, {"type": "rooms_list", "rooms": self.server.room_manager.list_rooms()})

    def on_ready(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        if not room:
            return
        with room.lock:
            player.ready = packet.get("status", True)
        self._broadcast_players(room)

    def on_start(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        if not room:
            return
        if room.host != player.username:
            self.send(player, {"type": "error", "msg": "Only host can start"})
            return
        if not room.can_start():
            self.send(player, {"type": "error",
                                "msg": f"Need at least {MIN_PLAYERS} players and all must be READY."})
            return

        with room.lock:
            room.started = True
            room.game.assign_roles()
            room.game.phase = Phase.NIGHT
            room.game.round = 1

        # Persist sessions for reconnect
        for p in room.game.players.values():
            set_connected(p.username, room.name, True)

        # Deliver private role info
        for p in room.game.players.values():
            self.send(p, {"type": "role_assigned", "role": p.role.value,
                           "team": "werewolf" if p.role == Role.WEREWOLF else "good"})

        wolves = room.game.get_werewolves()
        for uname in wolves:
            self.send(room.game.players[uname], {"type": "wolf_team", "wolves": wolves})

        duration = PHASE_DURATION[Phase.NIGHT]
        self.broadcast(room, {"type": "phase_change", "phase": "night", "round": 1,
                               "duration": duration,
                               "msg": "Night falls. Werewolves, choose your victim."})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.NIGHT, duration, self._night_timeout)

    # ------------------------------------------------------------------ #
    #  Chat                                                                #
    # ------------------------------------------------------------------ #

    def on_chat(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        msg  = packet.get("msg", "").strip()
        if not msg:
            return
        if not player.alive:
            for p in room.game.players.values():
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

    # ------------------------------------------------------------------ #
    #  Night actions                                                       #
    # ------------------------------------------------------------------ #

    def on_kill(self, player, packet):
        if not player.room or player.role != Role.WEREWOLF:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT:
            return
        target = packet.get("target", "")
        with room.lock:
            if target in room.game.players and room.game.players[target].alive:
                room.game.night_kills[player.username] = target
        self.broadcast_wolves(room, {"type": "system",
                                      "msg": f"{player.username} targeted {target}"})

    def on_protect(self, player, packet):
        if not player.room or player.role != Role.DOCTOR:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT:
            return
        target = packet.get("target", "")
        with room.lock:
            if target in room.game.players and room.game.players[target].alive:
                room.game.night_protect = target
        self.send(player, {"type": "system", "msg": f"You chose to protect {target}."})

    def on_check(self, player, packet):
        if not player.room or player.role != Role.SEER:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT:
            return
        with room.lock:
            if player.username in room.game.seer_check:
                self.send(player, {"type": "error", "msg": "You have already used your vision this night."})
                return
        target = packet.get("target", "")
        with room.lock:
            if target not in room.game.players:
                return
            target_p = room.game.players[target]
            room.game.seer_check[player.username] = target
        is_wolf = target_p.role == Role.WEREWOLF
        self.send(player, {"type": "seer_result", "target": target,
                            "is_werewolf": is_wolf, "role": target_p.role.value,
                            "msg": f"{target} is a {target_p.role.value}."})

    # ------------------------------------------------------------------ #
    #  Voting                                                              #
    # ------------------------------------------------------------------ #

    def on_vote(self, player, packet):
        if not player.room or not player.alive:
            return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.VOTING:
            return
        target = packet.get("target", "")
        with room.lock:
            if target not in room.game.players or not room.game.players[target].alive:
                return
            room.game.votes[player.username] = target
            vote_counts = {}
            for t in room.game.votes.values():
                vote_counts[t] = vote_counts.get(t, 0) + 1
            alive_count = len(room.game.get_alive_players())
            votes_in    = len(room.game.votes)

        self.broadcast(room, {"type": "system", "msg": f"{player.username} has voted."})
        self.broadcast(room, {"type": "vote_update", "vote_counts": vote_counts,
                               "votes_in": votes_in, "total": alive_count})

    # ------------------------------------------------------------------ #
    #  Hunter                                                              #
    # ------------------------------------------------------------------ #

    def on_hunter_shot(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        with room.lock:
            if room.game.hunter_pending != player.username:
                return
            target = packet.get("target", "")
            if target not in room.game.players or not room.game.players[target].alive:
                self.send(player, {"type": "error", "msg": "Invalid target"})
                return
            room.game.players[target].alive = False
            room.game.eliminated.append(target)
            room.game.hunter_pending = None

        self.broadcast(room, {"type": "eliminated", "player": target,
                               "role": room.game.players[target].role.value,
                               "msg": f"Hunter {player.username} shot {target}!"})
        self._broadcast_players(room)
        winner = room.game.check_win()
        if winner:
            self._announce_winner(room, winner)
        else:
            self._start_next_night(room)

    # ------------------------------------------------------------------ #
    #  Ping / latency                                                      #
    # ------------------------------------------------------------------ #

    def on_ping(self, player, packet):
        player.last_ping = time.time()
        self.send(player, {"type": "pong", "t": packet.get("t", 0),
                            "server_time": time.time()})

    def on_players(self, player, packet):
        if not player.room:
            return
        room = self.server.room_manager.get_room(player.room)
        if room:
            self._broadcast_players(room)

    # ------------------------------------------------------------------ #
    #  Phase timer helpers                                                 #
    # ------------------------------------------------------------------ #

    def _start_phase_timer(self, room, phase, duration, callback):
        rname      = room.name
        cancel_ev  = threading.Event()
        self._cancel_timer(room)
        self._phase_timers[rname] = cancel_ev
        end_time   = time.time() + duration

        def countdown():
            while not cancel_ev.is_set():
                remaining = int(end_time - time.time())
                if remaining <= 0:
                    break
                r = self.server.room_manager.get_room(rname)
                if r and r.started and r.game.phase == phase:
                    r.game.phase_timer = remaining
                    self.broadcast(r, {"type": "timer", "seconds": remaining,
                                       "phase": phase.value, "duration": duration})
                cancel_ev.wait(1.0)
            if not cancel_ev.is_set():
                r = self.server.room_manager.get_room(rname)
                if r and r.started and r.game.phase == phase:
                    callback(r)

        threading.Thread(target=countdown, daemon=True).start()

    def _cancel_timer(self, room):
        ev = self._phase_timers.get(room.name)
        if ev:
            ev.set()

    # ------------------------------------------------------------------ #
    #  Night resolution                                                    #
    # ------------------------------------------------------------------ #

    def _night_timeout(self, room):
        if room.game.phase != Phase.NIGHT:
            return
        with room.lock:
            kills   = list(room.game.night_kills.values())
            protect = room.game.night_protect
            victim  = None
            if kills:
                from collections import Counter
                chosen = Counter(kills).most_common(1)[0][0]
                if chosen != protect:
                    victim = chosen
                    room.game.players[victim].alive = False
                    room.game.eliminated.append(victim)

        winner = room.game.check_win()
        if winner:
            self._announce_winner(room, winner)
            return

        # Check if victim was Hunter
        if victim and room.game.players[victim].role == Role.HUNTER:
            self._trigger_hunter(room, victim)
        else:
            self._advance_to_day(room, victim)

    def _trigger_hunter(self, room, hunter_username):
        with room.lock:
            room.game.hunter_pending = hunter_username
        self.broadcast(room, {"type": "eliminated", "player": hunter_username,
                               "role": Role.HUNTER.value,
                               "msg": f"{hunter_username} (Hunter) was eliminated — they may fire a shot!"})
        self._broadcast_players(room)
        hunter_p = room.game.players.get(hunter_username)
        if hunter_p and hunter_p.connected:
            self.send(hunter_p, {"type": "hunter_prompt",
                                  "msg": "You were eliminated! Choose someone to shoot (20s)."})

        def hunter_timeout():
            time.sleep(HUNTER_SHOT_TIMEOUT)
            with room.lock:
                still_pending = room.game.hunter_pending == hunter_username
                room.game.hunter_pending = None
            if still_pending:
                self.broadcast(room, {"type": "system",
                                       "msg": f"{hunter_username} did not shoot in time."})
                winner = room.game.check_win()
                if winner:
                    self._announce_winner(room, winner)
                else:
                    phase = room.game.phase
                    if phase == Phase.NIGHT:
                        # came from night
                        self._advance_to_day(room, hunter_username)
                    else:
                        self._start_next_night(room)

        threading.Thread(target=hunter_timeout, daemon=True).start()

    def _advance_to_day(self, room, victim):
        with room.lock:
            room.game.phase = Phase.DAY
        duration = PHASE_DURATION[Phase.DAY]
        msg = f"{victim} was killed last night." if victim else "No one was killed last night."
        self.broadcast(room, {"type": "phase_change", "phase": "day",
                               "duration": duration, "msg": msg})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.DAY, duration, self._advance_to_voting)

    def _advance_to_voting(self, room):
        with room.lock:
            room.game.phase = Phase.VOTING
            room.game.votes = {}
        duration = PHASE_DURATION[Phase.VOTING]
        self.broadcast(room, {"type": "phase_change", "phase": "voting",
                               "duration": duration, "msg": "Time to vote!"})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.VOTING, duration, self._resolve_vote)

    def _resolve_vote(self, room):
        if room.game.phase != Phase.VOTING:
            return
        self._cancel_timer(room)
        with room.lock:
            eliminated = room.game.tally_votes()
            if eliminated:
                room.game.players[eliminated].alive = False
                room.game.eliminated.append(eliminated)

        if eliminated:
            elim_role = room.game.players[eliminated].role.value
            self.broadcast(room, {"type": "eliminated", "player": eliminated,
                                   "role": elim_role,
                                   "msg": f"{eliminated} was voted out. They were a {elim_role}."})
        else:
            self.broadcast(room, {"type": "system", "msg": "It's a tie! Nobody eliminated."})

        self._broadcast_players(room)
        winner = room.game.check_win()
        if winner:
            self._announce_winner(room, winner)
            return

        # Hunter triggered by vote?
        if eliminated and room.game.players[eliminated].role == Role.HUNTER:
            self._trigger_hunter(room, eliminated)
            return

        self._start_next_night(room)

    def _start_next_night(self, room):
        with room.lock:
            room.game.round += 1
            room.game.reset_round()
            room.game.phase = Phase.NIGHT
        duration = PHASE_DURATION[Phase.NIGHT]
        self.broadcast(room, {"type": "phase_change", "phase": "night",
                               "round": room.game.round, "duration": duration,
                               "msg": "Night falls..."})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.NIGHT, duration, self._night_timeout)

    # ------------------------------------------------------------------ #
    #  Game over                                                           #
    # ------------------------------------------------------------------ #

    def _announce_winner(self, room, winner):
        roles = {p.username: p.role.value for p in room.game.players.values()}
        self.broadcast(room, {"type": "game_over", "winner": winner, "roles": roles})
        self.server.log(f"[GAME_OVER] room={room.name} winner={winner}")

        with room.lock:
            room.started        = False
            room.game.phase     = Phase.ENDED
            room.game.hunter_pending = None
        self._cancel_timer(room)

        for p in room.game.players.values():
            clear_session(p.username)
            p.ready = False
            p.alive = True
            p.role  = Role.VILLAGER

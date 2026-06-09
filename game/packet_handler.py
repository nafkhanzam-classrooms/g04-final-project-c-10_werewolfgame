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
        self.match_sessions = {} # room_name -> { mac: Player }

    def handle(self, player, packet):
        ptype = packet.get("type")
        handlers = {
            "identify":   self.on_identify,
            "login":      self.on_login,
            "create":     self.on_create,
            "join":       self.on_join,
            "leave":      self.on_leave,
            "rooms":      self.on_rooms,
            "start":      self.on_start,
            "ready":      self.on_ready,
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

    def on_identify(self, player, packet):
        mac = packet.get("mac", "")
        if not mac: return
        player.mac = mac

        # GLOBAL SEARCH: Find if this MAC is in any active match session
        for rname, sessions in self.match_sessions.items():
            if mac in sessions:
                old_p = sessions[mac]
                room = self.server.room_manager.get_room(rname)
                if not room: continue

                # Restore connection
                old_p.conn = player.conn
                old_p.connected = True
                
                # Notify client and server
                self.send(old_p, {"type": "login_ok", "username": old_p.username, "reconnect": True})
                self.send(old_p, {"type": "phase_change", "phase": room.game.phase.value, 
                                   "duration": room.game.phase_timer, "msg": "Session restored automatically!"})
                
                # Reveal private role and team info
                self.send(old_p, {"type": "role_assigned", "role": old_p.role.value,
                                   "team": "werewolf" if old_p.role == Role.WEREWOLF else "good"})
                
                if old_p.role == Role.WEREWOLF:
                    wolves = room.game.get_werewolves()
                    self.send(old_p, {"type": "wolf_team", "wolves": wolves})

                self._broadcast_players(room)
                self.broadcast(room, {"type": "system", "msg": f"{old_p.username} reconnected!"})
                
                # Replace mapping in server
                self.server.active_conns[player.addr] = old_p
                return
        
        # If not found, just let the client stay at main menu (already there)
        pass


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
        """Push updated player list to everyone in room."""
        players_info = [
            {"username": p.username if p.username else f"Guest_{p.addr[1]}", 
             "alive": p.alive, 
             "ready": p.ready,
             "connected": p.connected,
             "host": p.username == room.host}
            for p in room.game.players.values()
        ]
        self.broadcast(room, {"type": "players_list", "players": players_info,
                               "phase": room.game.phase.value})

    def _start_phase_timer(self, room, phase, duration, callback):
        rname = room.name
        cancel_ev = threading.Event()
        if rname in self._phase_timers:
            self._phase_timers[rname].set()
        self._phase_timers[rname] = cancel_ev
        end_time = time.time() + duration

        def countdown():
            while not cancel_ev.is_set():
                remaining = int(end_time - time.time())
                if remaining <= 0:
                    break
                r = self.server.room_manager.get_room(rname)
                if r and r.started and r.game.phase == phase:
                    r.game.phase_timer = remaining
                    self.broadcast(r, {"type": "timer", "seconds": remaining, "phase": phase.value, "duration": duration})
                cancel_ev.wait(1.0)
            if not cancel_ev.is_set():
                r = self.server.room_manager.get_room(rname)
                if r and r.started and r.game.phase == phase:
                    callback(r)

        t = threading.Thread(target=countdown, daemon=True)
        t.start()


    def on_login(self, player, packet):
        username = packet.get("username", "").strip()
        mac = packet.get("mac", "")
        player.mac = mac

        if not username or len(username) < 2:
            self.send(player, {"type": "error", "msg": "Username must be at least 2 characters"})
            return
        
        if not player.room:
            self.send(player, {"type": "error", "msg": "You must join a room first"})
            return

        room = self.server.room_manager.get_room(player.room)
        if not room:
            self.send(player, {"type": "error", "msg": "Room not found"})
            return

        # RECONNECT LOGIC: Check if this MAC is part of an active match session
        rname = room.name
        if rname in self.match_sessions and mac in self.match_sessions[rname]:
            old_p = self.match_sessions[rname][mac]
            if not old_p.connected:
                # Found a disconnected match player! Restore them.
                old_p.conn = player.conn
                old_p.connected = True
                
                # Remove the temporary Guest player from the room
                guest_key = f"Guest_{player.addr[1]}"
                room.remove_player(guest_key)
                
                # We need to tell the server to replace the 'current' player object with the restored one
                # for the current socket thread. 
                self.send(old_p, {"type": "login_ok", "username": old_p.username, "reconnect": True})
                self.send(old_p, {"type": "phase_change", "phase": room.game.phase.value, 
                                   "duration": room.game.phase_timer, "msg": "Welcome back!"})
                
                # Reveal private role to reconnected player
                self.send(old_p, {"type": "role_assigned", "role": old_p.role.value,
                                   "team": "werewolf" if old_p.role == Role.WEREWOLF else "good"})
                
                self._broadcast_players(room)
                self.broadcast(room, {"type": "system", "msg": f"{old_p.username} reconnected!"})
                
                # Notify server to use this old_p for future packets from this socket
                self.server.active_conns[player.addr] = old_p
                return

        # Normal login check within room
        for p in room.game.players.values():
            if p != player and p.username == username:
                self.send(player, {"type": "error", "msg": "Username already used in this room."})
                return

        old_key = player.username if player.username else f"Guest_{player.addr[1]}"
        if old_key in room.game.players:
            del room.game.players[old_key]
            
        player.username = username
        room.game.players[username] = player
        
        if room.host == "" or room.host == old_key:
            room.host = username
            
        self._broadcast_players(room)
        self.send(player, {"type": "login_ok", "username": username})

    def on_create(self, player, packet):
        if player.room: self.on_leave(player, {})
        name = packet.get("room", "").strip().upper()
        room, msg = self.server.room_manager.create_room(name, player)
        if room is None:
            self.send(player, {"type": "error", "msg": msg})
            return
        self._send_room_joined(player, room, host=True)

    def on_join(self, player, packet):
        if player.room: self.on_leave(player, {})
        name = packet.get("room", "").strip().upper()
        mac = packet.get("mac", "")
        player.mac = mac
        
        ignore_started = False
        if name in self.match_sessions and mac in self.match_sessions[name]:
            ignore_started = True

        room, msg = self.server.room_manager.join_room(name, player, ignore_started=ignore_started)
        if room is None:
            self.send(player, {"type": "error", "msg": msg})
            return
        self._send_room_joined(player, room, host=(room.host == player.username))
        self._broadcast_players(room)

    def _send_room_joined(self, player, room, host):
        players_info = [
            {"username": p.username if p.username else f"Guest_{p.addr[1]}", 
             "alive": p.alive, "ready": p.ready, "connected": p.connected, "host": p.username == room.host}
            for p in room.game.players.values()
        ]
        self.send(player, {"type": "room_joined", "room": room.name, "host": host,
                            "players": players_info})

    def on_leave(self, player, packet):
        if not player.room: return
        room = self.server.room_manager.get_room(player.room)
        rname = player.room
        
        # If match is active, remove from session memory
        if rname in self.match_sessions and player.mac in self.match_sessions[rname]:
            del self.match_sessions[rname][player.mac]

        player.ready = False
        player.username = ""
        self.server.room_manager.leave_room(player)
        if room:
            self._broadcast_players(room)
        self.send(player, {"type": "left_room", "room": rname})

    def on_rooms(self, player, packet):
        rooms = self.server.room_manager.list_rooms()
        self.send(player, {"type": "rooms_list", "rooms": rooms})

    def on_ready(self, player, packet):
        if not player.room: return
        room = self.server.room_manager.get_room(player.room)
        if not room: return
        player.ready = packet.get("status", True)
        self._broadcast_players(room)

    def on_start(self, player, packet):
        if not player.room: return
        room = self.server.room_manager.get_room(player.room)
        if room.host != player.username:
            self.send(player, {"type": "error", "msg": "Only host can start"})
            return
        if not room.can_start():
            self.send(player, {"type": "error", "msg": "Need at least 4 players and all must be READY."})
            return
        
        room.started = True
        room.game.assign_roles()
        room.game.phase = Phase.NIGHT
        room.game.round = 1

        # Initialize match session memory for reconnection
        self.match_sessions[room.name] = {p.mac: p for p in room.game.players.values() if p.mac}

        for p in room.game.players.values():
            self.send(p, {"type": "role_assigned", "role": p.role.value,
                           "team": "werewolf" if p.role == Role.WEREWOLF else "good"})

        wolves = room.game.get_werewolves()
        for uname in wolves:
            self.send(room.game.players[uname], {"type": "wolf_team", "wolves": wolves})

        duration = PHASE_DURATION[Phase.NIGHT]
        self.broadcast(room, {"type": "phase_change", "phase": "night", "round": 1,
                               "duration": duration, "msg": "Night falls. Werewolves, choose your victim."})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.NIGHT, duration, self._night_timeout)

    def on_chat(self, player, packet):
        if not player.room: return
        room = self.server.room_manager.get_room(player.room)
        msg = packet.get("msg", "").strip()
        if not msg: return
        if not player.alive:
            for p in room.game.players.values():
                if not p.alive:
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
        if not player.room or player.role != Role.WEREWOLF: return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT: return
        target = packet.get("target", "")
        if target in room.game.players and room.game.players[target].alive:
            room.game.night_kills[player.username] = target
            self.broadcast_wolves(room, {"type": "system", "msg": f"{player.username} targeted {target}"})

    def on_check(self, player, packet):
        if not player.room or player.role != Role.SEER: return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.NIGHT: return
        target = packet.get("target", "")
        if target in room.game.players:
            target_p = room.game.players[target]
            is_wolf = target_p.role == Role.WEREWOLF
            self.send(player, {"type": "seer_result", "target": target, "is_werewolf": is_wolf,
                                "role": target_p.role.value,
                                "msg": f"{target} is a {target_p.role.value}."})

    def on_vote(self, player, packet):
        if not player.room or not player.alive: return
        room = self.server.room_manager.get_room(player.room)
        if room.game.phase != Phase.VOTING: return
        target = packet.get("target", "")
        if target in room.game.players and room.game.players[target].alive:
            room.game.votes[player.username] = target
            self.broadcast(room, {"type": "system", "msg": f"{player.username} has voted."})
            alive = room.game.get_alive_players()
            if len(room.game.votes) >= len(alive):
                self._resolve_vote(room)

    def on_ping(self, player, packet):
        self.send(player, {"type": "pong", "t": packet.get("t", 0)})

    def on_players(self, player, packet):
        if not player.room: return
        room = self.server.room_manager.get_room(player.room)
        self._broadcast_players(room)


    def _night_timeout(self, room):
        if room.game.phase != Phase.NIGHT: return
        kills = list(room.game.night_kills.values())
        victim = None
        if kills:
            from collections import Counter
            victim = Counter(kills).most_common(1)[0][0]
            room.game.players[victim].alive = False
            room.game.eliminated.append(victim)
        
        winner = room.game.check_win()
        if winner:
            self._announce_winner(room, winner)
        else:
            self._advance_to_day(room, victim)

    def _advance_to_day(self, room, victim):
        room.game.phase = Phase.DAY
        duration = PHASE_DURATION[Phase.DAY]
        msg = f"{victim} was killed last night." if victim else "No one was killed last night."
        self.broadcast(room, {"type": "phase_change", "phase": "day", "duration": duration, "msg": msg})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.DAY, duration, self._advance_to_voting)

    def _advance_to_voting(self, room):
        room.game.phase = Phase.VOTING
        room.game.votes = {}
        duration = PHASE_DURATION[Phase.VOTING]
        self.broadcast(room, {"type": "phase_change", "phase": "voting", "duration": duration, "msg": "Time to vote!"})
        self._broadcast_players(room)
        self._start_phase_timer(room, Phase.VOTING, duration, self._resolve_vote)

    def _resolve_vote(self, room):
        if room.game.phase != Phase.VOTING: return
        rname = room.name
        if rname in self._phase_timers: self._phase_timers[rname].set()

        eliminated = room.game.tally_votes()
        if eliminated:
            room.game.players[eliminated].alive = False
            room.game.eliminated.append(eliminated)
            self.broadcast(room, {"type": "eliminated", "player": eliminated, "role": room.game.players[eliminated].role.value,
                                   "msg": f"{eliminated} was voted out. They were a {room.game.players[eliminated].role.value}."})
        else:
            self.broadcast(room, {"type": "system", "msg": "No majority. Nobody eliminated."})

        winner = room.game.check_win()
        if winner:
            self._announce_winner(room, winner)
        else:
            room.game.round += 1
            room.game.reset_round()
            room.game.phase = Phase.NIGHT
            duration = PHASE_DURATION[Phase.NIGHT]
            self.broadcast(room, {"type": "phase_change", "phase": "night", "round": room.game.round, "duration": duration, "msg": "Night falls..."})
            self._broadcast_players(room)
            self._start_phase_timer(room, Phase.NIGHT, duration, self._night_timeout)

    def _announce_winner(self, room, winner):
        roles = {p.username: p.role.value for p in room.game.players.values()}
        self.broadcast(room, {"type": "game_over", "winner": winner, "roles": roles})
        room.started = False
        room.game.phase = Phase.ENDED
        
        # MATCH OVER: Clear reconnection memory for this room
        if room.name in self.match_sessions:
            del self.match_sessions[room.name]

        # Reset all players
        for p in room.game.players.values():
            p.ready = False
            p.alive = True
            p.role = Role.VILLAGER
            
        if room.name in self._phase_timers: self._phase_timers[room.name].set()

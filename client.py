import pygame
import socket
import threading
import json
import time
import sys
import math
from datetime import datetime

# Config
HOST = "127.0.0.1"
PORT = 5000
FPS  = 60

# Dynamic resolution
BASE_W, BASE_H = 2200, 1440

# Palette
C = {
    "bg_night":   (6,  6, 16),
    "bg_day":     (10, 14, 28),
    "panel":      (18, 18, 32),
    "panel2":     (24, 24, 40),
    "panel3":     (30, 30, 50),
    "border":     (55, 55, 85),
    "border_hi":  (90, 90, 130),
    "white":      (235, 235, 248),
    "dim":        (110, 110, 145),
    "dim2":       (75, 75, 105),
    "accent":     (165, 90, 255),
    "accent_hi":  (200, 130, 255),
    "cyan":       (80, 195, 255),
    "cyan_hi":    (130, 220, 255),
    "red":        (215, 55, 55),
    "red_dim":    (140, 35, 35),
    "red_hi":     (255, 90, 90),
    "gold":       (215, 175, 55),
    "gold_hi":    (255, 210, 90),
    "green":      (55, 195, 95),
    "green_hi":   (90, 230, 130),
    "wolf":       (200, 45, 45),
    "wolf_hi":    (240, 80, 80),
    "seer":       (70, 140, 225),
    "seer_hi":    (110, 175, 255),
    "villager":   (65, 190, 115),
    "villager_hi":(100, 225, 150),
    "timer_ok":   (55, 195, 95),
    "timer_warn": (215, 175, 55),
    "timer_crit": (215, 55, 55),
}

def encode(data):
    return (json.dumps(data) + "\n").encode("utf-8")

def ts():
    return datetime.now().strftime("%H:%M:%S")

# Network
class Net:
    def __init__(self):
        self.sock = None
        self.connected = False
        self._buf = ""
        self._q = []
        self._lock = threading.Lock()

    def connect(self, host, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((host, port))
        self.sock.settimeout(None)
        self.connected = True
        threading.Thread(target=self._recv, daemon=True).start()

    def send(self, d):
        if self.connected:
            try:
                self.sock.sendall(encode(d))
            except Exception:
                self.connected = False

    def _recv(self):
        while self.connected:
            try:
                data = self.sock.recv(8192)
                if not data:
                    self.connected = False
                    break
                self._buf += data.decode("utf-8", errors="ignore")
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            pkt = json.loads(line)
                            with self._lock:
                                self._q.append(pkt)
                        except Exception:
                            pass
            except Exception:
                self.connected = False
                break

    def poll(self):
        with self._lock:
            items = list(self._q)
            self._q.clear()
        return items

    def ping(self):
        self.send({"type": "ping", "t": time.time()})


# UI
def draw_rect_alpha(surf, color, rect, alpha=180, radius=8):
    s = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
    pygame.draw.rect(s, (*color, alpha), (0, 0, rect[2], rect[3]), border_radius=radius)
    surf.blit(s, (rect[0], rect[1]))

def draw_panel(surf, x, y, w, h, bg=None, border=None, radius=10):
    bg    = bg     or C["panel"]
    border= border or C["border"]
    pygame.draw.rect(surf, bg,     (x, y, w, h), border_radius=radius)
    pygame.draw.rect(surf, border, (x, y, w, h), 1, border_radius=radius)

def draw_text(surf, text, font, color, x, y, cx=False, cy=False, max_w=None):
    if max_w:
        # truncate
        while font.size(text)[0] > max_w and len(text) > 4:
            text = text[:-4] + "…"
    t = font.render(str(text), True, color)
    rx = x - t.get_width()//2  if cx else x
    ry = y - t.get_height()//2 if cy else y
    surf.blit(t, (rx, ry))
    return t.get_width()

def lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Button:
    def __init__(self, rect, label, bg, bg_h=None, fg=None, font=None, radius=8):
        self.rect   = pygame.Rect(rect)
        self.label  = label
        self.bg     = bg
        self.bg_h   = bg_h or tuple(min(255, c+45) for c in bg)
        self.fg     = fg  or C["white"]
        self.font   = font
        self.radius = radius
        self.enabled= True
        self._hover = False
        self._press = 0.0   # animation

    def update(self, mx, my, dt):
        self._hover = self.rect.collidepoint(mx, my) and self.enabled
        target = 1.0 if self._hover else 0.0
        self._press += (target - self._press) * min(1.0, dt * 12)

    def draw(self, surf):
        bg = lerp_color(self.bg, self.bg_h, self._press) if self.enabled else C["border"]
        pygame.draw.rect(surf, bg, self.rect, border_radius=self.radius)
        border_c = C["white"] if self.enabled else C["dim2"]
        pygame.draw.rect(surf, border_c, self.rect, 1, border_radius=self.radius)
        if self.font:
            fg = self.fg if self.enabled else C["dim"]
            draw_text(surf, self.label, self.font, fg,
                      self.rect.centerx, self.rect.centery, cx=True, cy=True)

    def clicked(self, event):
        return (self.enabled and event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1 and self.rect.collidepoint(event.pos))


class InputBox:
    def __init__(self, rect, placeholder="", font=None, max_len=48, password=False):
        self.rect   = pygame.Rect(rect)
        self.text   = ""
        self.ph     = placeholder
        self.font   = font
        self.max_len= max_len
        self.pw     = password
        self.active = False
        self._cursor_t = 0.0

    def handle(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
        if event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key not in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_TAB):
                if len(self.text) < self.max_len:
                    self.text += event.unicode
            return event.key == pygame.K_RETURN
        return False

    def update(self, dt):
        self._cursor_t += dt

    def draw(self, surf):
        bc = C["accent"] if self.active else C["border"]
        pygame.draw.rect(surf, C["bg_night"], self.rect, border_radius=6)
        pygame.draw.rect(surf, bc,            self.rect, 2, border_radius=6)
        if self.font:
            disp  = ("•" * len(self.text) if self.pw else self.text) if self.text else self.ph
            color = C["white"] if self.text else C["dim"]
            txt   = self.font.render(disp, True, color)
            py    = self.rect.y + (self.rect.h - txt.get_height()) // 2
            surf.blit(txt, (self.rect.x + 12, py))
            # cursor
            if self.active and int(self._cursor_t * 2) % 2 == 0:
                cx = self.rect.x + 12 + txt.get_width() + 2
                pygame.draw.line(surf, C["accent"], (cx, py+2), (cx, py+txt.get_height()-2), 2)

    def clear(self):
        self.text = ""


# PlayerCard
class PlayerCard:
    """Clickable player card in the player panel."""
    def __init__(self, username, role_hint=""):
        self.username  = username
        self.alive     = True
        self.is_host   = False
        self.role_hint = role_hint  # only shown if we know it
        self.rect      = pygame.Rect(0, 0, 0, 0)
        self._hover    = False
        self._anim     = 0.0

    def update(self, mx, my, dt):
        self._hover = self.rect.collidepoint(mx, my) and self.alive
        t = 1.0 if self._hover else 0.0
        self._anim += (t - self._anim) * min(1, dt * 14)

    def draw(self, surf, font_name, font_sm, font_xs, action_label, action_color,
             is_me=False, vote_tally=0, total_votes=0):
        x, y, w, h = self.rect

        # Background
        if not self.alive:
            bg     = (12, 12, 22)
            border = C["dim2"]
        elif is_me:
            bg     = lerp_color(C["panel2"], C["panel3"], self._anim)
            border = lerp_color(C["accent"], C["accent_hi"], self._anim)
        else:
            bg     = lerp_color(C["panel"], C["panel2"], self._anim)
            border = lerp_color(C["border"], C["border_hi"], self._anim)

        pygame.draw.rect(surf, bg,     self.rect, border_radius=10)
        pygame.draw.rect(surf, border, self.rect, 2, border_radius=10)

        # Avatar circle
        av_r  = h // 2 - 8
        av_cx = x + 16 + av_r
        av_cy = y + h // 2
        av_c  = C["dim2"] if not self.alive else (C["wolf"] if "Wolf" in self.role_hint else
                (C["seer"] if "Seer" in self.role_hint else C["accent"]))
        pygame.draw.circle(surf, av_c, (av_cx, av_cy), av_r)
        init = self.username[0].upper()
        draw_text(surf, init, font_name, C["white"], av_cx, av_cy, cx=True, cy=True)

        # Name
        name_x = av_cx + av_r + 14
        name_c = C["gold"] if is_me else (C["dim"] if not self.alive else C["white"])
        draw_text(surf, self.username, font_name, name_c, name_x, y + 10, max_w=w - 200)

        # Tags
        tags = []
        if is_me:
            tags.append(("YOU", C["gold"]))
        if self.is_host:
            tags.append(("HOST", C["cyan"]))
        if not self.alive:
            tags.append(("DEAD", C["dim"]))
        if self.role_hint:
            rc = C["wolf"] if "Wolf" in self.role_hint else (C["seer"] if "Seer" in self.role_hint else C["villager"])
            tags.append((self.role_hint.upper(), rc))

        tx = name_x
        for tag, tc in tags:
            tw = font_xs.size(tag)[0] + 10
            tag_rect = pygame.Rect(tx, y + h - 26, tw, 18)
            pygame.draw.rect(surf, (*tc, 40), tag_rect, border_radius=4)
            pygame.draw.rect(surf, tc, tag_rect, 1, border_radius=4)
            draw_text(surf, tag, font_xs, tc, tag_rect.centerx, tag_rect.centery, cx=True, cy=True)
            tx += tw + 6

        # Vote bar
        if vote_tally > 0 and total_votes > 0:
            bar_w  = w - 20
            bar_h  = 5
            bar_y  = y + h - 6
            fill_w = int(bar_w * vote_tally / total_votes)
            pygame.draw.rect(surf, C["dim2"],   (x + 10, bar_y, bar_w, bar_h), border_radius=2)
            pygame.draw.rect(surf, C["red_hi"], (x + 10, bar_y, fill_w, bar_h), border_radius=2)
            draw_text(surf, f"{vote_tally}✗", font_xs, C["red_hi"],
                      x + w - 50, bar_y - 3)

        # Action button
        if action_label and self.alive and not is_me:
            btn_w  = 110
            btn_h  = 36
            btn_x  = x + w - btn_w - 10
            btn_y  = y + (h - btn_h) // 2
            btn_r  = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
            hover  = btn_r.collidepoint(pygame.mouse.get_pos())
            bg2    = lerp_color(action_color, tuple(min(255,c+50) for c in action_color),
                                1.0 if hover else 0.0)
            pygame.draw.rect(surf, bg2,      btn_r, border_radius=6)
            pygame.draw.rect(surf, C["white"],btn_r, 1,  border_radius=6)
            draw_text(surf, action_label, font_sm, C["white"],
                      btn_r.centerx, btn_r.centery, cx=True, cy=True)
            return btn_r   # return for click detection
        return None


# Timer Widget
def draw_timer(surf, cx, cy, radius, seconds, total, phase, font_big, font_sm):
    if total <= 0:
        return
    frac  = max(0.0, seconds / total)
    angle = -math.pi/2
    sweep = 2 * math.pi * frac

    # Track
    pygame.draw.circle(surf, C["panel2"], (cx, cy), radius, 6)

    # Arc (draw as polygon approx)
    if frac > 0.001:
        color = C["timer_ok"] if frac > 0.4 else (C["timer_warn"] if frac > 0.2 else C["timer_crit"])
        pts = []
        steps = max(6, int(60 * frac))
        for i in range(steps + 1):
            a = angle + sweep * i / steps
            pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        if len(pts) >= 2:
            pygame.draw.lines(surf, color, False, pts, 8)

        # Glow dot at end
        ea  = angle + sweep
        ex  = cx + radius * math.cos(ea)
        ey  = cy + radius * math.sin(ea)
        pygame.draw.circle(surf, color, (int(ex), int(ey)), 10)

    # Center text
    draw_text(surf, str(seconds), font_big, C["white"], cx, cy, cx=True, cy=True)
    phase_labels = {"night": "NIGHT", "day": "DAY", "voting": "VOTE"}
    draw_text(surf, phase_labels.get(phase, phase.upper()), font_sm, C["dim"],
              cx, cy + 32, cx=True)


# Main Client
class WerewolfGame:
    def __init__(self):
        pygame.init()
        info = pygame.display.Info()
        # Use 90% of screen or BASE dimensions
        sw = min(BASE_W, int(info.current_w * 0.93))
        sh = min(BASE_H, int(info.current_h * 0.93))
        self.W, self.H = sw, sh
        self.screen = pygame.display.set_mode((self.W, self.H), pygame.RESIZABLE)
        pygame.display.set_caption("🐺  Werewolf: Azrael of the Night")
        self.clock  = pygame.time.Clock()

        # Scale factor relative to BASE
        self.sx = self.W / BASE_W
        self.sy = self.H / BASE_H

        self._init_fonts()
        self._init_state()
        self._init_ui()

        # Auto-connect
        self._do_connect()

    def _s(self, n):
        """Scale a size value."""
        return max(1, int(n * min(self.sx, self.sy)))

    def _init_fonts(self):
        s = min(self.sx, self.sy)
        self.f_title = pygame.font.SysFont("monospace", int(52*s), bold=True)
        self.f_lg    = pygame.font.SysFont("monospace", int(34*s), bold=True)
        self.f_md    = pygame.font.SysFont("monospace", int(24*s))
        self.f_sm    = pygame.font.SysFont("monospace", int(20*s))
        self.f_xs    = pygame.font.SysFont("monospace", int(16*s))
        self.f_xxs   = pygame.font.SysFont("monospace", int(13*s))
        self.f_timer = pygame.font.SysFont("monospace", int(42*s), bold=True)

    def _init_state(self):
        self.net          = Net()
        self.state        = "login"   # login, lobby, game
        self.username     = ""
        self.role         = ""
        self.team         = ""
        self.room         = ""
        self.is_host      = False
        self.phase        = "lobby"
        self.round_n      = 0
        self.alive        = True
        self.players      = []   # list of dicts from server
        self.wolves       = []
        self.vote_tally   = {}   # target -> count
        self.voted        = False
        self.seer_checked = set()
        self.seer_results = {}   # target -> bool (is_wolf)
        self.kill_target  = None  # wolf's current selection
        self.messages     = []   # (text, color, time)
        self.max_msgs     = 150
        self.ping_ms      = 0
        self.last_ping    = 0.0
        self.timer_sec    = 0
        self.timer_total  = 0
        self.notif        = ""
        self.notif_t      = 0.0
        self.rooms_list   = []
        self.game_over    = None  # {"winner":..,"roles":..}
        self._refresh_t   = 0.0
        self._anim_t      = 0.0

    def _init_ui(self):
        W, H = self.W, self.H
        cx   = W // 2

        # Login
        iw = self._s(420)
        ih = self._s(58)
        self.inp_user = InputBox((cx - iw//2, H//2 - ih//2, iw, ih),
                                  "Enter your name…", self.f_md)
        self.btn_enter = Button((cx - self._s(120), H//2 + self._s(50), self._s(240), self._s(58)),
                                 "ENTER GAME", C["accent"], font=self.f_md)

        # Lobby
        rw = self._s(460)
        self.inp_room = InputBox((self._s(20), self._s(110), rw, ih),
                                  "Room name…", self.f_md)
        bw = self._s(160)
        bh = self._s(52)
        self.btn_create  = Button((self._s(20)+rw+self._s(14), self._s(108), bw, bh), "CREATE",  C["green"],  font=self.f_sm)
        self.btn_join    = Button((self._s(20)+rw+bw+self._s(28), self._s(108), bw, bh), "JOIN", C["cyan"],   font=self.f_sm)
        self.btn_refresh = Button((self._s(20)+rw+bw*2+self._s(42), self._s(108), bw, bh), "REFRESH", C["panel3"], font=self.f_sm)
        self.btn_start   = Button((W//2 - self._s(160), H - self._s(90), self._s(320), self._s(68)), "▶  START GAME", C["green"], font=self.f_lg)
        self.btn_leave   = Button((W - self._s(200), H - self._s(90), self._s(180), self._s(60)), "LEAVE", C["red_dim"], font=self.f_sm)

        # Game chat input
        chat_h = self._s(56)
        cw     = int(W * 0.52)
        self.inp_chat = InputBox((self._s(10), H - chat_h - self._s(10), cw - self._s(20), chat_h),
                                  "Type message…", self.f_sm, max_len=120)
        self.btn_send = Button((cw - self._s(10), H - chat_h - self._s(10), self._s(120), chat_h),
                                "SEND", C["accent"], font=self.f_sm)

        # Player cards (will be rebuilt dynamically)
        self.player_cards = {}   # username -> PlayerCard

    def _do_connect(self):
        try:
            self.net.connect(HOST, PORT)
            self.notify("Connected to server!", C["green"])
        except Exception as e:
            self.notify(f"Cannot connect: {e}  (is server running?)", C["red"])

    # Main Loop

    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            self._handle_events(dt)
            self._process_net()
            self._update(dt)
            self._draw()
            pygame.display.flip()

    def _handle_events(self, dt):
        mx, my = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.VIDEORESIZE:
                self.W, self.H = event.w, event.h
                self.sx = self.W / BASE_W
                self.sy = self.H / BASE_H
                self.screen = pygame.display.set_mode((self.W, self.H), pygame.RESIZABLE)
                self._init_fonts()
                self._init_ui()

            if self.state == "login":
                self._ev_login(event)
            elif self.state == "lobby":
                self._ev_lobby(event)
            elif self.state == "game":
                self._ev_game(event)

        # Hover updates
        if self.state == "login":
            self.btn_enter.update(mx, my, dt)
            self.inp_user.update(dt)
        elif self.state == "lobby":
            for btn in (self.btn_create, self.btn_join, self.btn_refresh,
                        self.btn_start, self.btn_leave):
                btn.update(mx, my, dt)
            self.inp_room.update(dt)
        elif self.state == "game":
            self.btn_send.update(mx, my, dt)
            self.inp_chat.update(dt)
            for card in self.player_cards.values():
                card.update(mx, my, dt)

    def _ev_login(self, event):
        submitted = self.inp_user.handle(event)
        if self.btn_enter.clicked(event) or submitted:
            uname = self.inp_user.text.strip()
            if len(uname) >= 2:
                self.net.send({"type": "login", "username": uname})
            else:
                self.notify("Name must be at least 2 characters", C["red"])

    def _ev_lobby(self, event):
        self.inp_room.handle(event)
        if self.btn_create.clicked(event):
            name = self.inp_room.text.strip()
            if name:
                self.net.send({"type": "create", "room": name})
                self.inp_room.clear()
        if self.btn_join.clicked(event):
            name = self.inp_room.text.strip()
            if name:
                self.net.send({"type": "join", "room": name})
                self.inp_room.clear()
        if self.btn_refresh.clicked(event):
            self.net.send({"type": "rooms"})
        if self.btn_start.clicked(event) and self.is_host and self.room:
            self.net.send({"type": "start"})
        if self.btn_leave.clicked(event) and self.room:
            self.net.send({"type": "leave"})
        # Click room from list
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for i, r in enumerate(self.rooms_list):
                row_y = self._s(200) + i * self._s(52)
                row_r = pygame.Rect(self._s(20), row_y, self.W - self._s(40), self._s(46))
                if row_r.collidepoint(mx, my) and r["status"] == "Waiting":
                    self.net.send({"type": "join", "room": r["name"]})

    def _ev_game(self, event):
        submitted = self.inp_chat.handle(event)
        if self.btn_send.clicked(event) or submitted:
            self._send_chat()
        # Player card action buttons
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for uname, card in self.player_cards.items():
                if uname == self.username:
                    continue
                if not card.alive:
                    continue
                btn_r = self._card_action_rect(card)
                if btn_r and btn_r.collidepoint(mx, my):
                    self._do_action(uname)

    def _card_action_rect(self, card):
        """Returns the action button rect for a card if one should show."""
        if not card.alive or card.username == self.username:
            return None
        x, y, w, h = card.rect
        bw = self._s(120); bh = self._s(38)
        return pygame.Rect(x + w - bw - self._s(10), y + (h - bh)//2, bw, bh)

    def _do_action(self, target):
        """Send appropriate action packet based on role/phase."""
        if self.phase == "voting" and self.alive and not self.voted:
            self.net.send({"type": "vote", "target": target})
            self.voted = True
            self.notify(f"Voted for {target}!", C["gold"])
        elif self.phase == "night":
            if self.role == "Werewolf" and self.alive:
                self.net.send({"type": "kill", "target": target})
                self.kill_target = target
                self.notify(f"Targeting {target}…", C["wolf"])
            elif self.role == "Seer" and self.alive and target not in self.seer_checked:
                self.net.send({"type": "check", "target": target})
                self.seer_checked.add(target)
                self.notify(f"Checking {target}…", C["seer"])

    def _send_chat(self):
        text = self.inp_chat.text.strip()
        self.inp_chat.clear()
        if not text:
            return
        self.net.send({"type": "chat", "msg": text})

    # Network Handling
    def _process_net(self):
        for pkt in self.net.poll():
            pt = pkt.get("type")
            fn = {
                "login_ok":     self._p_login,
                "room_joined":  self._p_room_joined,
                "left_room":    self._p_left_room,
                "rooms_list":   self._p_rooms_list,
                "chat":         self._p_chat,
                "system":       self._p_system,
                "phase_change": self._p_phase,
                "role_assigned":self._p_role,
                "wolf_team":    self._p_wolf_team,
                "vote_cast":    self._p_vote,
                "eliminated":   self._p_elim,
                "game_over":    self._p_game_over,
                "error":        self._p_error,
                "pong":         self._p_pong,
                "players_list": self._p_players,
                "seer_result":  self._p_seer,
                "wolf_action":  self._p_wolf_action,
                "kill_confirm": self._p_kill_confirm,
                "rejoin":       self._p_rejoin,
                "timer":        self._p_timer,
            }.get(pt)
            if fn:
                fn(pkt)

    def _p_login(self, p):
        self.username = p["username"]
        self.state    = "lobby"
        self.net.send({"type": "rooms"})
        self.add_msg(f"Welcome, {self.username}!", C["green"])

    def _p_room_joined(self, p):
        self.room     = p["room"]
        self.is_host  = p.get("host", False)
        self._update_players(p.get("players", []))
        self.add_msg(f"Joined room: {self.room}", C["cyan"])

    def _p_left_room(self, p):
        self.room     = ""
        self.is_host  = False
        self.phase    = "lobby"
        self.role     = ""
        self.alive    = True
        self.voted    = False
        self.game_over= None
        self.state    = "lobby"
        self.net.send({"type": "rooms"})

    def _p_rooms_list(self, p):
        self.rooms_list = p.get("rooms", [])

    def _p_chat(self, p):
        sender = p.get("sender","?")
        msg    = p.get("msg","")
        if p.get("wolf_chat"):
            self.add_msg(f"🐺 [Wolf] {sender}: {msg}", C["wolf"])
        elif p.get("dead"):
            self.add_msg(f"👻 [Dead] {sender}: {msg}", C["dim"])
        else:
            self.add_msg(f"💬 {sender}: {msg}", C["white"])

    def _p_system(self, p):
        self.add_msg(f"⚡ {p.get('msg','')}", C["accent"])

    def _p_phase(self, p):
        phase    = p.get("phase","")
        msg      = p.get("msg","")
        duration = p.get("duration", 0)
        self.phase       = phase
        self.round_n     = p.get("round", self.round_n)
        self.timer_total = duration
        self.timer_sec   = duration
        self.voted       = False
        self.kill_target = None
        self.vote_tally  = {}
        if phase == "night":
            self.seer_checked = set()

        colors = {"night":C["accent"],"day":C["gold"],"voting":C["red"],"ended":C["green"]}
        c = colors.get(phase, C["white"])
        sep = "─" * 50
        self.add_msg(sep, C["border"])
        self.add_msg(f"  ◈ {phase.upper()} PHASE  —  Round {self.round_n}", c)
        self.add_msg(f"  {msg}", C["white"])
        self.add_msg(sep, C["border"])

        if self.state != "game" and self.room:
            self.state = "game"

    def _p_role(self, p):
        self.role = p.get("role","")
        self.team = p.get("team","")
        rc = C["wolf"] if self.team=="werewolf" else (C["seer"] if self.role=="Seer" else C["villager"])
        self.add_msg("═"*50, rc)
        self.add_msg(f"  YOUR ROLE: {self.role}", rc)
        hints = {
            "Werewolf": "Click a player at night to KILL them.",
            "Seer":     "Click a player at night to REVEAL their role.",
            "Villager": "Discuss and vote out the werewolves!",
        }
        self.add_msg(f"  {hints.get(self.role,'')}", C["dim"])
        self.add_msg("═"*50, rc)
        self.state = "game"

    def _p_wolf_team(self, p):
        self.wolves = p.get("wolves", [])
        self.add_msg(f"🐺 Your pack: {', '.join(self.wolves)}", C["wolf"])

    def _p_vote(self, p):
        voter  = p.get("voter","")
        target = p.get("target","")
        votes  = p.get("votes", {})
        self.add_msg(f"🗳️  {voter} → {target}", C["gold"])
        # rebuild tally
        tally = {}
        for v in votes.values():
            tally[v] = tally.get(v, 0) + 1
        self.vote_tally = tally
        self.net.send({"type": "players"})

    def _p_elim(self, p):
        player = p.get("player","")
        role   = p.get("role","")
        self.add_msg(f"💀 {p.get('msg','')}", C["red"])
        # update role hint
        if player in self.player_cards:
            self.player_cards[player].role_hint = role
            self.player_cards[player].alive = False
        if player == self.username:
            self.alive = False
            self.add_msg("You were eliminated. Watch the game unfold…", C["dim"])
        self.net.send({"type": "players"})

    def _p_game_over(self, p):
        self.game_over = p
        winner = p.get("winner","")
        roles  = p.get("roles", {})
        # reveal all roles in cards
        for uname, role in roles.items():
            if uname in self.player_cards:
                self.player_cards[uname].role_hint = role
        self.add_msg("═"*50, C["gold"])
        self.add_msg(f"  🏆 GAME OVER — {winner.upper()}S WIN!", C["gold"])
        for uname, role in roles.items():
            c = C["wolf"] if role == "Werewolf" else C["villager"]
            self.add_msg(f"    {uname}: {role}", c)
        self.add_msg("═"*50, C["gold"])
        self.phase = "ended"
        self.timer_sec = 0

    def _p_error(self, p):
        self.notify(p.get("msg","Error"), C["red"])

    def _p_pong(self, p):
        t = p.get("t", 0)
        if t:
            self.ping_ms = int((time.time() - t) * 1000)

    def _p_players(self, p):
        self._update_players(p.get("players", []))
        ph = p.get("phase","")
        if ph:
            self.phase = ph

    def _p_seer(self, p):
        target  = p.get("target","")
        is_wolf = p.get("is_werewolf", False)
        self.seer_results[target] = is_wolf
        c = C["wolf"] if is_wolf else C["green"]
        self.add_msg(f"🔮 {p.get('msg','')}", c)
        if target in self.player_cards:
            self.player_cards[target].role_hint = "Werewolf" if is_wolf else "Innocent"

    def _p_wolf_action(self, p):
        self.add_msg(f"🐺 {p.get('msg','')}", C["wolf_hi"])

    def _p_kill_confirm(self, p):
        self.kill_target = p.get("target","")
        self.add_msg(f"🎯 {p.get('msg','')}", C["wolf"])

    def _p_rejoin(self, p):
        self.room    = p.get("room","")
        self.phase   = p.get("phase","")
        self.round_n = p.get("round", 0)
        self._update_players(p.get("players", []))
        self.state   = "game"
        self.net.send({"type": "players"})
        self.add_msg(f"Rejoined room {self.room}", C["green"])

    def _p_timer(self, p):
        self.timer_sec = p.get("seconds", 0)

    def _update_players(self, plist):
        if not plist:
            return
        # Keep existing cards, update data
        seen = set()
        for pd in plist:
            uname = pd["username"]
            seen.add(uname)
            if uname not in self.player_cards:
                self.player_cards[uname] = PlayerCard(uname)
            card = self.player_cards[uname]
            card.alive   = pd.get("alive", True)
            card.is_host = pd.get("host", False)
        # Update alive flag for self
        for pd in plist:
            if pd["username"] == self.username:
                self.alive = pd.get("alive", True)
        self.players = plist

    def add_msg(self, text, color=None):
        color = color or C["white"]
        self.messages.append((f"[{ts()}] {text}", color, time.time()))
        if len(self.messages) > self.max_msgs:
            self.messages.pop(0)

    def notify(self, msg, color=None):
        self.notif   = msg
        self.notif_t = 4.0
        self.add_msg(f"[!] {msg}", color or C["gold"])

    # Update
    def _update(self, dt):
        self._anim_t += dt
        if self.notif_t > 0:
            self.notif_t -= dt
        # ping
        now = time.time()
        if now - self.last_ping > 4 and self.net.connected:
            self.net.ping()
            self.last_ping = now
        # periodic room/player refresh
        self._refresh_t += dt
        if self._refresh_t > 3.0:
            self._refresh_t = 0.0
            if self.state == "lobby":
                self.net.send({"type": "rooms"})
            elif self.state == "game" and self.room:
                self.net.send({"type": "players"})

    # Draw 
    def _draw(self):
        W, H = self.W, self.H
        if self.state == "login":
            self._draw_login()
        elif self.state == "lobby":
            self._draw_lobby()
        elif self.state == "game":
            self._draw_game()
        self._draw_notif()
        # Connection status dot
        dot_c = C["green"] if self.net.connected else C["red"]
        pygame.draw.circle(self.screen, dot_c, (W - self._s(14), self._s(14)), self._s(8))

    def _draw_bg(self, phase="night"):
        W, H = self.W, self.H
        bg = C["bg_night"] if phase == "night" else C["bg_day"]
        self.screen.fill(bg)
        # Starfield / subtle grid
        gc = (14, 14, 28) if phase == "night" else (16, 20, 36)
        step = self._s(80)
        for x in range(0, W, step):
            pygame.draw.line(self.screen, gc, (x, 0), (x, H))
        for y in range(0, H, step):
            pygame.draw.line(self.screen, gc, (0, y), (W, y))

    # ── Login Screen ───────────────────────────────────────────
    def _draw_login(self):
        W, H = self.W, self.H
        self._draw_bg("night")
        cx = W // 2

        # Animated glow
        glow_r = int(200 + 30 * math.sin(self._anim_t * 0.8))
        glow_s = pygame.Surface((glow_r*2, glow_r*2), pygame.SRCALPHA)
        pygame.draw.circle(glow_s, (165, 90, 255, 18), (glow_r, glow_r), glow_r)
        self.screen.blit(glow_s, (cx - glow_r, H//2 - self._s(200) - glow_r))

        draw_text(self.screen, "🐺  WEREWOLF", self.f_title, C["accent"],
                  cx, H//2 - self._s(230), cx=True)
        draw_text(self.screen, "AZRAEL  OF  THE  NIGHT", self.f_sm, C["dim"],
                  cx, H//2 - self._s(165), cx=True)

        # Panel
        pw, ph = self._s(520), self._s(280)
        draw_panel(self.screen, cx - pw//2, H//2 - self._s(80), pw, ph)

        draw_text(self.screen, "Enter your name to join", self.f_md, C["dim"],
                  cx, H//2 - self._s(60), cx=True)

        # Reposition inputs
        iw = self._s(420)
        ih = self._s(58)
        self.inp_user.rect = pygame.Rect(cx - iw//2, H//2 - ih//2 + self._s(10), iw, ih)
        self.inp_user.draw(self.screen)

        bw = self._s(240); bh = self._s(58)
        self.btn_enter.rect = pygame.Rect(cx - bw//2, H//2 + self._s(60), bw, bh)
        self.btn_enter.draw(self.screen)

        # Hint
        conn_c = C["green"] if self.net.connected else C["red"]
        conn_s = f"● Server {HOST}:{PORT}  connected" if self.net.connected else f"● Cannot connect to {HOST}:{PORT}"
        draw_text(self.screen, conn_s, self.f_xs, conn_c, cx, H//2 + self._s(150), cx=True)

    # ── Lobby Screen ───────────────────────────────────────────
    def _draw_lobby(self):
        W, H = self.W, self.H
        self._draw_bg("day")

        # Header
        draw_panel(self.screen, 0, 0, W, self._s(88), C["panel"], C["border"])
        draw_text(self.screen, "🐺  WEREWOLF  LOBBY", self.f_lg, C["accent"],
                  W//2, self._s(28), cx=True)
        draw_text(self.screen, f"  {self.username}  |  ping {self.ping_ms}ms",
                  self.f_sm, C["dim"], W - self._s(340), self._s(32))

        # Room input row
        y0 = self._s(100)
        rw = self._s(460); ih = self._s(58)
        self.inp_room.rect = pygame.Rect(self._s(20), y0, rw, ih)
        self.inp_room.draw(self.screen)
        bw = self._s(160); bh = self._s(52)
        ox = self._s(20) + rw + self._s(14)
        self.btn_create.rect  = pygame.Rect(ox,            y0+3, bw, bh)
        self.btn_join.rect    = pygame.Rect(ox+bw+self._s(14), y0+3, bw, bh)
        self.btn_refresh.rect = pygame.Rect(ox+bw*2+self._s(28), y0+3, bw, bh)
        for btn in (self.btn_create, self.btn_join, self.btn_refresh):
            btn.draw(self.screen)

        # ── Two columns ─────────────────────────────────────
        col_y  = y0 + ih + self._s(20)
        col_h  = H - col_y - self._s(100)
        mid    = W // 2 - self._s(20)

        # LEFT: room list
        draw_panel(self.screen, self._s(10), col_y, mid - self._s(10), col_h)
        draw_text(self.screen, "Available Rooms", self.f_md, C["cyan"],
                  self._s(24), col_y + self._s(12))

        if not self.rooms_list:
            draw_text(self.screen, "No rooms yet — create one!", self.f_sm, C["dim"],
                      self._s(24), col_y + self._s(60))
        for i, r in enumerate(self.rooms_list[:16]):
            ry     = col_y + self._s(50) + i * self._s(52)
            row_r  = pygame.Rect(self._s(20), ry, mid - self._s(30), self._s(46))
            mx, my = pygame.mouse.get_pos()
            hovered= row_r.collidepoint(mx, my)
            bg = C["panel3"] if hovered else C["panel2"]
            pygame.draw.rect(self.screen, bg, row_r, border_radius=8)
            pygame.draw.rect(self.screen, C["border"], row_r, 1, border_radius=8)
            waiting = "Waiting" in r["status"]
            sc = C["green"] if waiting else C["dim"]
            draw_text(self.screen, r["name"],   self.f_md, C["white"],  row_r.x+self._s(14), ry+self._s(12), max_w=mid//2)
            draw_text(self.screen, f"{r['players']}/{r['max']}", self.f_sm, C["cyan"],   row_r.x+mid//2, ry+self._s(14))
            draw_text(self.screen, r["status"], self.f_sm, sc,          row_r.right-self._s(170), ry+self._s(14))
            if waiting and hovered:
                draw_text(self.screen, "Click to join", self.f_xs, C["dim"], row_r.right-self._s(180), ry+self._s(14))

        # RIGHT: current room
        draw_panel(self.screen, mid + self._s(30), col_y, W - mid - self._s(40), col_h)
        if self.room:
            rx = mid + self._s(44)
            draw_text(self.screen, f"📍  {self.room}", self.f_lg, C["cyan"], rx, col_y + self._s(14))
            host_s = "  (You are host)" if self.is_host else ""
            draw_text(self.screen, f"Players: {len(self.players)}{host_s}", self.f_sm, C["dim"], rx, col_y + self._s(60))

            for i, pd in enumerate(self.players[:10]):
                py2 = col_y + self._s(100) + i * self._s(44)
                c = C["gold"] if pd["username"] == self.username else C["white"]
                host_t = "  👑" if pd.get("host") else ""
                draw_text(self.screen, f"  ●  {pd['username']}{host_t}", self.f_md, c, rx, py2)

            # Start hint
            if self.is_host:
                min_p = 2
                if len(self.players) >= min_p:
                    draw_text(self.screen, "✓ Ready to start!", self.f_sm, C["green"],
                              rx, col_y + col_h - self._s(110))
                else:
                    draw_text(self.screen, f"Need {min_p - len(self.players)} more player(s)",
                              self.f_sm, C["dim"], rx, col_y + col_h - self._s(110))
            else:
                draw_text(self.screen, "Waiting for host to start…", self.f_sm, C["dim"],
                          rx, col_y + col_h - self._s(110))
        else:
            draw_text(self.screen, "Create or join a room", self.f_md, C["dim"],
                      mid + self._s(44), col_y + self._s(60))

        # Bottom buttons
        self.btn_start.enabled = self.is_host and bool(self.room) and len(self.players) >= 2
        bw2 = self._s(320); bh2 = self._s(68)
        self.btn_start.rect = pygame.Rect(W//2 - bw2//2, H - bh2 - self._s(14), bw2, bh2)
        if self.room:
            self.btn_start.draw(self.screen)
            lw = self._s(160); lh = self._s(54)
            self.btn_leave.rect = pygame.Rect(W - lw - self._s(20), H - lh - self._s(20), lw, lh)
            self.btn_leave.draw(self.screen)

    def _draw_game(self):
        W, H = self.W, self.H
        night = self.phase == "night"
        self._draw_bg("night" if night else "day")

        # Player panel width: dynamic based on player count
        n_players = max(len(self.players), 1)
        card_h    = self._s(80)
        card_pad  = self._s(8)
        needed_h  = n_players * (card_h + card_pad) + self._s(160)
        pp_w      = max(self._s(380), min(self._s(560), int(W * 0.28)))
        # Timer panel
        tp_w      = self._s(220)
        tp_h      = self._s(220)
        # Chat panel
        chat_w    = W - pp_w - tp_w - self._s(20)
        chat_h_in = self._s(70)

        # Phase colors
        phase_colors = {"night":C["accent"],"day":C["gold"],"voting":C["red"],"ended":C["green"]}
        ph_c = phase_colors.get(self.phase, C["white"])

        bar_h = self._s(70)
        draw_panel(self.screen, 0, 0, W, bar_h, C["panel"], ph_c)
        # Phase
        ph_label = {"night":"🌙 NIGHT","day":"☀️  DAY","voting":"🗳️  VOTING",
                    "ended":"🏆 ENDED"}.get(self.phase, self.phase.upper())
        draw_text(self.screen, ph_label, self.f_lg, ph_c, self._s(20), bar_h//2, cy=True)
        draw_text(self.screen, f"Round {self.round_n}", self.f_md, C["dim"],
                  self._s(340), bar_h//2, cy=True)
        # Role badge
        rc  = C["wolf"] if self.role=="Werewolf" else (C["seer"] if self.role=="Seer" else C["villager"])
        ral = "ALIVE" if self.alive else "DEAD"
        rac = C["green"] if self.alive else C["red"]
        draw_text(self.screen, f"{self.role}", self.f_md, rc, W//2, bar_h//2 - self._s(10), cx=True, cy=True)
        draw_text(self.screen, ral, self.f_xs, rac, W//2, bar_h//2 + self._s(14), cx=True, cy=True)
        # Ping + room
        draw_text(self.screen, f"📍 {self.room}  |  {self.username}  |  {self.ping_ms}ms",
                  self.f_xs, C["dim"], W - self._s(450), bar_h//2, cy=True)

        chat_x = 0
        chat_y = bar_h + self._s(6)
        chat_panel_h = H - bar_h - self._s(12)
        draw_panel(self.screen, chat_x, chat_y, chat_w, chat_panel_h - chat_h_in - self._s(14))

        # Hint bar
        hints = {
            "night":  ("🌙  NIGHT: Click a player to act | Wolves can chat only with wolves", C["accent"]),
            "day":    ("☀️   DAY: Discuss freely! Chat below. Timer counts down to vote phase.", C["gold"]),
            "voting": ("🗳️   VOTE: Click a player card button to vote them out!", C["red"]),
            "ended":  ("🏆  Game over. Type anything or type /leave", C["green"]),
        }
        ht, hc = hints.get(self.phase, ("", C["dim"]))
        draw_panel(self.screen, chat_x, chat_y, chat_w, self._s(32), C["panel2"], hc)
        draw_text(self.screen, ht, self.f_xs, hc, chat_x + self._s(10), chat_y + self._s(7),
                  max_w=chat_w - self._s(20))

        # Messages
        msg_y_start = chat_y + self._s(36)
        msg_h       = self._s(22)
        visible_n   = (chat_panel_h - chat_h_in - self._s(60) - self._s(36)) // msg_h
        visible     = self.messages[-visible_n:]
        for i, (msg, color, _) in enumerate(visible):
            draw_text(self.screen, msg, self.f_xxs, color,
                      chat_x + self._s(8), msg_y_start + i * msg_h,
                      max_w=chat_w - self._s(16))

        # Chat input
        inp_y = H - chat_h_in - self._s(8)
        inp_w = chat_w - self._s(140)
        self.inp_chat.rect = pygame.Rect(chat_x + self._s(4), inp_y, inp_w, chat_h_in - self._s(8))
        self.inp_chat.draw(self.screen)
        sw = self._s(120)
        self.btn_send.rect = pygame.Rect(chat_x + inp_w + self._s(8), inp_y, sw, chat_h_in - self._s(8))
        self.btn_send.draw(self.screen)

        tp_x = chat_w + self._s(8)
        tp_y = bar_h + self._s(6)
        draw_panel(self.screen, tp_x, tp_y, tp_w, tp_h + self._s(20), C["panel"])

        timer_cx = tp_x + tp_w // 2
        timer_cy = tp_y + (tp_h + self._s(20)) // 2
        draw_timer(self.screen, timer_cx, timer_cy,
                   self._s(75), self.timer_sec, self.timer_total,
                   self.phase, self.f_timer, self.f_sm)

        pp_x = chat_w + self._s(8)
        pp_y = tp_y + tp_h + self._s(30)
        pp_h = H - pp_y - self._s(4)
        draw_panel(self.screen, pp_x, pp_y, pp_w, pp_h)

        draw_text(self.screen, f"PLAYERS  ({len(self.players)})", self.f_md, C["cyan"],
                  pp_x + pp_w//2, pp_y + self._s(12), cx=True)
        pygame.draw.line(self.screen, C["border"],
                         (pp_x + self._s(10), pp_y + self._s(38)),
                         (pp_x + pp_w - self._s(10), pp_y + self._s(38)))

        # Determine action label per role/phase
        if self.phase == "voting" and self.alive and not self.voted:
            action_label = "VOTE"
            action_color = C["red"]
        elif self.phase == "night" and self.alive:
            if self.role == "Werewolf":
                action_label = "KILL"
                action_color = C["wolf"]
            elif self.role == "Seer":
                action_label = "CHECK"
                action_color = C["seer"]
            else:
                action_label = None
                action_color = C["dim"]
        else:
            action_label = None
            action_color = C["dim"]

        total_votes = sum(self.vote_tally.values()) if self.vote_tally else 1

        cy2 = pp_y + self._s(46)
        for pd in self.players:
            uname = pd["username"]
            if uname not in self.player_cards:
                self.player_cards[uname] = PlayerCard(uname)
            card = self.player_cards[uname]
            card.alive   = pd.get("alive", True)
            card.is_host = pd.get("host", False)

            cw2 = pp_w - self._s(12)
            card.rect = pygame.Rect(pp_x + self._s(6), cy2, cw2, card_h)

            # Determine action availability
            alab = None
            if action_label and card.alive and uname != self.username:
                if self.phase == "night" and self.role == "Seer" and uname in self.seer_checked:
                    alab = None  # already checked
                elif self.phase == "night" and self.role == "Werewolf" and uname in self.wolves:
                    alab = None  # can't kill teammate
                else:
                    alab = action_label

            vt = self.vote_tally.get(uname, 0)
            card.draw(self.screen, self.f_sm, self.f_sm, self.f_xxs,
                      alab, action_color,
                      is_me=(uname == self.username),
                      vote_tally=vt, total_votes=total_votes)
            cy2 += card_h + card_pad

        if self.game_over:
            self._draw_game_over_overlay()

    def _draw_game_over_overlay(self):
        if not self.game_over:
            return
        W, H = self.W, self.H
        # Dim background
        dim_s = pygame.Surface((W, H), pygame.SRCALPHA)
        dim_s.fill((0, 0, 0, 160))
        self.screen.blit(dim_s, (0, 0))

        winner = self.game_over.get("winner","")
        roles  = self.game_over.get("roles", {})
        wc     = C["wolf"] if winner == "Werewolf" else C["villager"]

        pw = self._s(700); ph = min(self._s(200) + len(roles)*self._s(52), int(H*0.8))
        px = W//2 - pw//2; py = H//2 - ph//2
        draw_panel(self.screen, px, py, pw, ph, C["panel"], wc, radius=16)

        draw_text(self.screen, f"🏆  {winner.upper()}S  WIN!", self.f_title, wc,
                  W//2, py + self._s(40), cx=True)
        draw_text(self.screen, "Final Roles:", self.f_md, C["dim"], W//2, py + self._s(100), cx=True)
        for i, (uname, role) in enumerate(roles.items()):
            rc = C["wolf"] if role == "Werewolf" else C["villager"]
            draw_text(self.screen, f"{uname}  →  {role}", self.f_md, rc,
                      W//2, py + self._s(140) + i * self._s(48), cx=True)

        # Leave button
        bw = self._s(260); bh = self._s(62)
        leave_r = pygame.Rect(W//2 - bw//2, py + ph - bh - self._s(20), bw, bh)
        mx, my = pygame.mouse.get_pos()
        hov = leave_r.collidepoint(mx, my)
        bg = C["accent_hi"] if hov else C["accent"]
        pygame.draw.rect(self.screen, bg, leave_r, border_radius=8)
        pygame.draw.rect(self.screen, C["white"], leave_r, 1, border_radius=8)
        draw_text(self.screen, "Back to Lobby", self.f_md, C["white"],
                  leave_r.centerx, leave_r.centery, cx=True, cy=True)
        # Check click — handled via polling is tricky; check here
        keys = pygame.mouse.get_pressed()
        if hov and keys[0] and not hasattr(self, "_leave_clicked"):
            self._leave_clicked = True
            self.net.send({"type": "leave"})
        if not keys[0]:
            self._leave_clicked = False

    def _draw_notif(self):
        if self.notif_t > 0 and self.notif:
            W, H = self.W, self.H
            alpha = min(255, int(self.notif_t * 90))
            nw = self._s(700); nh = self._s(52)
            s = pygame.Surface((nw, nh), pygame.SRCALPHA)
            s.fill((20, 10, 10, min(200, alpha)))
            pygame.draw.rect(s, (*C["red"], min(255, alpha)), (0, 0, nw, nh), 2, border_radius=8)
            txt = self.f_sm.render(self.notif[:80], True, C["white"])
            s.blit(txt, (14, (nh - txt.get_height())//2))
            self.screen.blit(s, (W//2 - nw//2, H - nh - self._s(80)))


if __name__ == "__main__":
    WerewolfGame().run()
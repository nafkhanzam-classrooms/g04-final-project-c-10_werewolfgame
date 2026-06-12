import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import socket
import threading
import json
import queue
import time

HOST = "143.198.217.44"
PORT = 5000
MAX_LINE_BYTES = 64 * 1024   # cap a single packet line at 64KB

THEMES = {
    "lobby":  {"bg": "#1a1a2e", "panel": "#16213e", "accent": "#00d2ff", "text": "#e1e1e1"},
    "night":  {"bg": "#0f0c29", "panel": "#302b63", "accent": "#e94560", "text": "#ffffff"},
    "day":    {"bg": "#ece9e6", "panel": "#ffffff", "accent": "#243b55", "text": "#333333"},
    "voting": {"bg": "#434343", "panel": "#000000", "accent": "#ff4d4d", "text": "#ffffff"},
    "ended":  {"bg": "#1a1a2e", "panel": "#16213e", "accent": "#ffd700", "text": "#ffffff"},
}


class NetClient:
    def __init__(self, host, port, packet_queue):
        self.host         = host
        self.port         = port
        self.packet_queue = packet_queue
        self.sock         = None
        self.connected    = False
        self.running      = False

    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
            self.connected = True
            self.running   = True
            threading.Thread(target=self._recv_loop, daemon=True).start()
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    def send(self, data: dict):
        if self.connected:
            try:
                self.sock.sendall((json.dumps(data) + "\n").encode("utf-8"))
            except Exception as e:
                print(f"Send error: {e}")
                self.connected = False

    def _recv_loop(self):
        buffer = ""
        while self.running:
            try:
                data = self.sock.recv(4096)
                if not data:
                    self.connected = False
                    break
                buffer += data.decode("utf-8", errors="ignore")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        print(f"[CLIENT] Oversized line ({len(line)}B), dropped")
                        continue
                    try:
                        self.packet_queue.put(json.loads(line))
                    except Exception:
                        # Malformed JSON from server — just skip it.
                        pass

                # Drop an oversized incomplete buffer.
                if len(buffer) > MAX_LINE_BYTES:
                    print(f"[CLIENT] Oversized buffer ({len(buffer)}B), dropped")
                    buffer = ""
            except Exception:
                self.connected = False
                break
        self.running = False

    def disconnect(self):
        self.running   = False
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


class WerewolfClient(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title("Werewolf: Azrael of the Night")
        self.geometry("1000x750")
        self.configure(bg="#1a1a2e")

        self.packet_queue = queue.Queue()
        self.net          = NetClient(HOST, PORT, self.packet_queue)

        # State
        self.username     = ""
        self.room_code    = ""
        self.is_host      = False
        self.players      = []
        self.phase        = "lobby"
        self.role         = ""
        self.alive        = True
        self.timer        = 0
        self.timer_total  = 60
        self.is_ready       = False
        self.game_results   = None
        self.ping_ms        = "--"
        self.seer_used      = False   # reset each night phase

        self.current_frame = None
        self._setup_styles()

        if not self.net.connect():
            messagebox.showerror("Error", "Could not connect to server")
            self.destroy()
            return

        self.deiconify()
        self.show_auth()

        # Ping heartbeat thread
        threading.Thread(target=self._ping_loop, daemon=True).start()

        self.after(100, self._process_packets)

    # ------------------------------------------------------------------ #
    #  Ping                                                                #
    # ------------------------------------------------------------------ #

    def _ping_loop(self):
        while True:
            time.sleep(10)
            if self.net.connected and self.username:
                self._ping_sent_at = time.time()
                self.net.send({"type": "ping", "t": self._ping_sent_at})

    # ------------------------------------------------------------------ #
    #  Styles                                                              #
    # ------------------------------------------------------------------ #

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        self.font_title  = ("Helvetica", 32, "bold")
        self.font_header = ("Helvetica", 20, "bold")
        self.font_main   = ("Helvetica", 12)
        self.font_bold   = ("Helvetica", 12, "bold")
        self.font_chat   = ("Courier", 11)
        self.font_timer  = ("Helvetica", 18, "bold")

        style.configure("TFrame",  background="#1a1a2e")
        style.configure("TLabel",  background="#1a1a2e", foreground="#e1e1e1", font=self.font_main)
        style.configure("TButton", font=self.font_bold, padding=10)
        style.configure("Timer.Horizontal.TProgressbar",
                        thickness=15, troughcolor="#16213e", background="#00d2ff",
                        bordercolor="#16213e", lightcolor="#00d2ff", darkcolor="#00d2ff")

    def update_theme(self, phase):
        theme = THEMES.get(phase, THEMES["lobby"])
        self.configure(bg=theme["bg"])
        if self.current_frame and hasattr(self.current_frame, "apply_theme"):
            self.current_frame.apply_theme(theme)

    def switch_frame(self, frame_class, *args, **kwargs):
        if self.current_frame:
            self.current_frame.destroy()
        self.current_frame = frame_class(self, *args, **kwargs)
        self.current_frame.pack(fill="both", expand=True)
        self.update_theme(self.phase)

    # ------------------------------------------------------------------ #
    #  Packet processing                                                  #
    # ------------------------------------------------------------------ #

    def _process_packets(self):
        while not self.packet_queue.empty():
            self._handle_packet(self.packet_queue.get())
        self.after(100, self._process_packets)

    def _handle_packet(self, packet):
        ptype = packet.get("type")

        if ptype == "register_ok":
            messagebox.showinfo("Registered", f"Account '{packet['username']}' created! Please log in.")

        elif ptype == "login_ok":
            self.username = packet["username"]
            if packet.get("reconnect"):
                self.show_game_screen()
            else:
                self.show_lobby()

        elif ptype == "state_snapshot":
            self.phase       = packet.get("phase", self.phase)
            self.role        = packet.get("role", self.role)
            self.timer       = packet.get("time_remaining", 0)
            self.timer_total = self.timer
            snap_players     = packet.get("players", [])
            if snap_players:
                self.players = snap_players
            if hasattr(self.current_frame, "update_phase"):
                self.current_frame.update_phase({"phase": self.phase, "duration": self.timer_total})
            if hasattr(self.current_frame, "set_role"):
                self.current_frame.set_role(self.role)
            if hasattr(self.current_frame, "update_players"):
                self.current_frame.update_players(self.players)

        elif ptype == "room_joined":
            self.room_code = packet["room"]
            self.is_host   = packet.get("host", False)
            self.players   = packet.get("players", [])
            self.show_room_lobby()

        elif ptype == "players_list":
            self.players = packet.get("players", [])
            self.phase   = packet.get("phase", "lobby")
            for p in self.players:
                if p["username"] == self.username:
                    self.is_host = p.get("host", False)
            if hasattr(self.current_frame, "update_players"):
                self.current_frame.update_players(self.players)

        elif ptype == "phase_change":
            self.phase       = packet["phase"]
            self.timer_total = packet.get("duration", 60)
            if self.phase == "night":
                self.seer_used = False
            if self.phase not in ("lobby", "ended"):
                if not isinstance(self.current_frame, GameFrame):
                    self.show_game_screen()
            self.update_theme(self.phase)
            if hasattr(self.current_frame, "update_phase"):
                self.current_frame.update_phase(packet)

        elif ptype == "timer":
            self.timer = packet["seconds"]
            if "duration" in packet:
                self.timer_total = packet["duration"]
            if hasattr(self.current_frame, "update_timer"):
                self.current_frame.update_timer(self.timer)

        elif ptype == "chat":
            if hasattr(self.current_frame, "add_chat"):
                self.current_frame.add_chat(packet)

        elif ptype == "system":
            if hasattr(self.current_frame, "add_system_msg"):
                self.current_frame.add_system_msg(packet.get("msg", ""))

        elif ptype == "role_assigned":
            self.role = packet["role"]
            if hasattr(self.current_frame, "set_role"):
                self.current_frame.set_role(self.role)

        elif ptype == "wolf_team":
            if hasattr(self.current_frame, "add_system_msg"):
                wolves = ", ".join(packet.get("wolves", []))
                self.current_frame.add_system_msg(f"Your wolf pack: {wolves}")

        elif ptype == "seer_result":
            if hasattr(self.current_frame, "add_seer_result"):
                self.current_frame.add_seer_result(packet)

        elif ptype == "vote_update":
            if hasattr(self.current_frame, "update_vote_counts"):
                self.current_frame.update_vote_counts(packet)

        elif ptype == "eliminated":
            if hasattr(self.current_frame, "add_system_msg"):
                self.current_frame.add_system_msg(packet.get("msg", ""))
            if hasattr(self.current_frame, "update_players"):
                self.current_frame.update_players(self.players)

        elif ptype == "hunter_prompt":
            if self.role == "Hunter" and isinstance(self.current_frame, GameFrame):
                self.current_frame.show_hunter_dialog()

        elif ptype == "pong":
            rtt = (time.time() - packet.get("t", time.time())) * 1000
            self.ping_ms = f"{rtt:.0f}ms"
            if hasattr(self.current_frame, "update_ping"):
                self.current_frame.update_ping(self.ping_ms)

        elif ptype == "game_over":
            self.game_results = packet
            self.phase        = "ended"
            self.show_game_over()

        elif ptype == "error":
            messagebox.showerror("Error", packet.get("msg", "Unknown error"))

        elif ptype == "left_room":
            self.room_code = ""
            self.phase     = "lobby"
            self.show_lobby()

    # ------------------------------------------------------------------ #
    #  Frame navigation                                                    #
    # ------------------------------------------------------------------ #

    def show_auth(self):
        self.phase = "lobby"
        self.switch_frame(AuthFrame)

    def show_lobby(self):
        self.phase = "lobby"
        self.switch_frame(LobbyFrame)

    def show_room_lobby(self):
        self.phase = "lobby"
        self.switch_frame(RoomLobbyFrame)

    def show_game_screen(self):
        self.switch_frame(GameFrame)

    def show_game_over(self):
        self.switch_frame(GameOverFrame)


# ====================================================================== #
#  Auth Frame — Register / Login                                          #
# ====================================================================== #

class AuthFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        tk.Label(self, text="WEREWOLF", font=master.font_title,
                 bg=master["bg"], fg="#00d2ff").pack(pady=(80, 5))
        tk.Label(self, text="Azrael of the Night", font=master.font_main,
                 bg=master["bg"], fg="#e1e1e1").pack(pady=(0, 40))

        form = tk.Frame(self, bg=master["bg"])
        form.pack()

        tk.Label(form, text="Username:", font=master.font_bold,
                 bg=master["bg"], fg="#e1e1e1").grid(row=0, column=0, sticky="e", pady=8, padx=10)
        self.user_entry = ttk.Entry(form, font=master.font_main, width=22)
        self.user_entry.grid(row=0, column=1, pady=8)

        tk.Label(form, text="Password:", font=master.font_bold,
                 bg=master["bg"], fg="#e1e1e1").grid(row=1, column=0, sticky="e", pady=8, padx=10)
        self.pass_entry = ttk.Entry(form, font=master.font_main, width=22, show="*")
        self.pass_entry.grid(row=1, column=1, pady=8)
        self.pass_entry.bind("<Return>", lambda e: self.do_login())

        btn_frame = tk.Frame(self, bg=master["bg"])
        btn_frame.pack(pady=30)
        ttk.Button(btn_frame, text="Login",    width=14, command=self.do_login).pack(side="left", padx=10)
        ttk.Button(btn_frame, text="Register", width=14, command=self.do_register).pack(side="left", padx=10)

    def _get_fields(self):
        u = self.user_entry.get().strip()
        p = self.pass_entry.get().strip()
        if not u or not p:
            messagebox.showwarning("Warning", "Username and password are required")
            return None, None
        return u, p

    def do_login(self):
        u, p = self._get_fields()
        if u:
            self.master.net.send({"type": "login", "username": u, "password": p})

    def do_register(self):
        u, p = self._get_fields()
        if u:
            self.master.net.send({"type": "register", "username": u, "password": p})


# ====================================================================== #
#  Lobby Frame — create / join room                                       #
# ====================================================================== #

class LobbyFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        tk.Label(self, text=f"Welcome, {master.username}", font=master.font_header,
                 bg=master["bg"], fg="#00d2ff").pack(pady=(100, 30))

        tk.Label(self, text="Enter Room Code to Join:", font=master.font_main,
                 bg=master["bg"], fg="#e1e1e1").pack()
        self.room_entry = ttk.Entry(self, font=master.font_header, width=15, justify="center")
        self.room_entry.pack(pady=10)

        btn_frame = tk.Frame(self, bg=master["bg"])
        btn_frame.pack(pady=30)
        ttk.Button(btn_frame, text="Join Room",      width=18, command=self.join_room).pack(side="left", padx=15)
        ttk.Button(btn_frame, text="Create New Room", width=18, command=self.create_room).pack(side="left", padx=15)

    def join_room(self):
        code = self.room_entry.get().strip().upper()
        if not code:
            messagebox.showwarning("Warning", "Please enter a room code")
            return
        self.master.net.send({"type": "join", "room": code})

    def create_room(self):
        self.master.net.send({"type": "create", "room": "AUTO"})


# ====================================================================== #
#  Room Lobby Frame — waiting room                                        #
# ====================================================================== #

class RoomLobbyFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        header = tk.Frame(self, bg=master["bg"])
        header.pack(fill="x", pady=40, padx=60)

        tk.Label(header, text=f"ROOM: {master.room_code}", font=master.font_header,
                 bg=master["bg"], fg="#00d2ff").pack(side="left")
        self.count_label = tk.Label(header, text="0 / 4 minimum players",
                                     font=master.font_main, bg=master["bg"], fg="#e1e1e1")
        self.count_label.pack(side="right")

        list_frame = tk.Frame(self, bg="#16213e", padx=30, pady=30,
                              highlightthickness=2, highlightbackground="#00d2ff")
        list_frame.pack(fill="both", expand=True, padx=60, pady=10)

        tk.Label(list_frame, text="PLAYERS IN LOBBY:", font=master.font_bold,
                 bg="#16213e", fg="#e1e1e1").pack(anchor="w", pady=(0, 15))
        self.player_listbox = tk.Listbox(list_frame, font=master.font_bold,
                                          bg="#16213e", fg="white",
                                          borderwidth=0, highlightthickness=0,
                                          selectbackground="#16213e")
        self.player_listbox.pack(fill="both", expand=True)

        self.footer = tk.Frame(self, bg=master["bg"])
        self.footer.pack(fill="x", pady=40, padx=60)

        self.ready_btn = ttk.Button(self.footer, text="Ready", width=15, command=self.toggle_ready)
        self.ready_btn.pack(side="left", padx=10)

        self.start_btn = None
        if master.is_host:
            self.start_btn = ttk.Button(self.footer, text="Start Game", width=15, command=self.start_game)
            self.start_btn.pack(side="left", padx=10)

        ttk.Button(self.footer, text="Leave Room", width=15, command=self.leave_room).pack(side="right", padx=10)

        self.update_players(master.players)

    def update_players(self, players):
        self.player_listbox.delete(0, tk.END)
        for p in players:
            ready_tag = "✅ READY" if p.get("ready") else "❌ NOT READY"
            host_tag  = " 👑" if p.get("host") else ""
            if p["username"] == self.master.username:
                self.master.is_ready = p.get("ready", False)
                self.ready_btn.config(text="UNREADY" if self.master.is_ready else "READY")
            self.player_listbox.insert(
                tk.END, f"  {p['username']}{host_tag}".ljust(30) + ready_tag
            )
        self.count_label.config(text=f"{len(players)} / 4 minimum players")

        # Sync host button visibility
        if self.master.is_host and self.start_btn is None:
            self.start_btn = ttk.Button(self.footer, text="Start Game", width=15, command=self.start_game)
            self.start_btn.pack(side="left", padx=10, after=self.ready_btn)
        elif not self.master.is_host and self.start_btn is not None:
            self.start_btn.destroy()
            self.start_btn = None

    def toggle_ready(self):
        self.master.net.send({"type": "ready", "status": not self.master.is_ready})

    def start_game(self):
        self.master.net.send({"type": "start"})

    def leave_room(self):
        self.master.net.send({"type": "leave"})


# ====================================================================== #
#  Game Frame                                                             #
# ====================================================================== #

class GameFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master
        self._hunter_dialog = None

        # ---- Top bar ----
        self.top_bar = tk.Frame(self, bg="#16213e", height=100)
        self.top_bar.pack(fill="x")
        self.top_bar.pack_propagate(False)

        self.info_top = tk.Frame(self.top_bar, bg="#16213e")
        self.info_top.pack(fill="x", padx=30, pady=(15, 0))

        self.phase_label = tk.Label(self.info_top, text="NIGHT PHASE",
                                     font=master.font_header, bg="#16213e", fg="#e94560")
        self.phase_label.pack(side="left")

        self.timer_label = tk.Label(self.info_top, text="0s",
                                     font=master.font_timer, bg="#16213e", fg="#ffffff", padx=20)
        self.timer_label.pack(side="left")

        self.ping_label = tk.Label(self.info_top, text="Ping: --",
                                    font=master.font_main, bg="#16213e", fg="#888888", padx=10)
        self.ping_label.pack(side="left")

        self.role_label = tk.Label(self.info_top,
                                    text=f"ROLE: {master.role.upper()}",
                                    font=master.font_header, bg="#16213e", fg="#00d2ff")
        self.role_label.pack(side="right")

        timer_frame = tk.Frame(self.top_bar, bg="#16213e")
        timer_frame.pack(fill="x", padx=30, pady=(5, 15))
        self.timer_bar = ttk.Progressbar(timer_frame, orient="horizontal", mode="determinate",
                                          maximum=100, style="Timer.Horizontal.TProgressbar")
        self.timer_bar.pack(fill="x", expand=True)

        # ---- Content area ----
        self.content = tk.Frame(self, bg=master["bg"])
        self.content.pack(fill="both", expand=True)

        # Chat panel
        self.chat_frame = tk.Frame(self.content, bg=master["bg"], padx=20, pady=20)
        self.chat_frame.pack(side="left", fill="both", expand=True)

        tk.Label(self.chat_frame, text="REALTIME CHAT", font=master.font_bold,
                 bg=master["bg"], fg="#e1e1e1").pack(anchor="w", pady=(0, 10))

        self.chat_area = scrolledtext.ScrolledText(
            self.chat_frame, bg="#16213e", fg="white", font=master.font_chat,
            state="disabled", borderwidth=0, highlightthickness=1, highlightbackground="#333"
        )
        self.chat_area.pack(fill="both", expand=True)

        input_frame = tk.Frame(self.chat_frame, bg=master["bg"])
        input_frame.pack(fill="x", pady=(15, 0))

        self.msg_entry = ttk.Entry(input_frame, font=master.font_main)
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.msg_entry.bind("<Return>", lambda e: self.send_chat())
        ttk.Button(input_frame, text="Send", width=10, command=self.send_chat).pack(side="right")

        # Right panel — player list + vote tally
        self.right_frame = tk.Frame(self.content, bg=master["bg"], width=350, padx=20, pady=20)
        self.right_frame.pack(side="right", fill="y")
        self.right_frame.pack_propagate(False)

        tk.Label(self.right_frame, text="PLAYER STATUS", font=master.font_bold,
                 bg=master["bg"], fg="#e1e1e1").pack(pady=(0, 10))

        self.players_container = tk.Frame(self.right_frame, bg=master["bg"])
        self.players_container.pack(fill="x")

        # Vote tally (hidden until voting phase)
        self.vote_frame = tk.Frame(self.right_frame, bg=master["bg"])
        self.vote_frame.pack(fill="x", pady=(15, 0))
        self.vote_title = tk.Label(self.vote_frame, text="VOTE TALLY", font=master.font_bold,
                                    bg=master["bg"], fg="#ff4d4d")
        self.vote_title.pack(anchor="w")
        self.vote_text = tk.Label(self.vote_frame, text="", font=master.font_chat,
                                   bg=master["bg"], fg="#e1e1e1", justify="left")
        self.vote_text.pack(anchor="w")
        self.vote_frame.pack_forget()

        self.update_players(master.players)
        self.apply_theme(THEMES.get(master.phase, THEMES["lobby"]))

    def apply_theme(self, theme):
        self.configure(bg=theme["bg"])
        self.content.configure(bg=theme["bg"])
        self.chat_frame.configure(bg=theme["bg"])
        self.right_frame.configure(bg=theme["bg"])
        self.players_container.configure(bg=theme["bg"])
        self.vote_frame.configure(bg=theme["bg"])
        self.vote_title.configure(bg=theme["bg"])
        self.vote_text.configure(bg=theme["bg"])
        for widget in self.chat_frame.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(bg=theme["bg"], fg=theme["text"])
        for widget in self.right_frame.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(bg=theme["bg"], fg=theme["text"])
        self.top_bar.configure(bg=theme["panel"])
        self.info_top.configure(bg=theme["panel"])
        for w in self.info_top.winfo_children():
            w.configure(bg=theme["panel"])
        style = ttk.Style()
        style.configure("Timer.Horizontal.TProgressbar",
                         background=theme["accent"], troughcolor=theme["panel"])
        if self.master.phase == "day":
            self.phase_label.configure(fg="#243b55")
            self.chat_area.configure(bg="#ffffff", fg="#333333", highlightbackground="#cccccc")
        else:
            self.phase_label.configure(fg=theme["accent"])
            self.chat_area.configure(bg="#16213e", fg="#ffffff", highlightbackground="#444444")

    def update_players(self, players):
        for w in self.players_container.winfo_children():
            w.destroy()
        theme = THEMES.get(self.master.phase, THEMES["lobby"])
        for p in players:
            alive     = p.get("alive", True)
            connected = p.get("connected", True)
            icon      = "👤" if alive else "👻"
            conn_tag  = "" if connected else " [OFFLINE]"
            color     = theme["text"] if (alive and connected) else "#888888"
            name      = p["username"]
            is_me     = name == self.master.username
            if is_me:
                name += " (YOU)"
                self.master.alive = alive

            p_frame = tk.Frame(self.players_container, bg=theme["panel"], pady=8, padx=10,
                                highlightthickness=1, highlightbackground="#444")
            p_frame.pack(fill="x", pady=3)
            tk.Label(p_frame, text=f"{icon} {name}{conn_tag}",
                     bg=theme["panel"], fg=color, font=self.master.font_bold).pack(side="left")

            if self.master.alive and alive and connected and not is_me:
                phase = self.master.phase
                role  = self.master.role
                if phase == "voting":
                    ttk.Button(p_frame, text="VOTE", width=6,
                               command=lambda u=p["username"]: self.vote(u)).pack(side="right")
                elif phase == "night":
                    if role == "Werewolf":
                        ttk.Button(p_frame, text="KILL", width=6,
                                   command=lambda u=p["username"]: self.kill(u)).pack(side="right")
                    elif role == "Seer":
                        btn = ttk.Button(p_frame, text="CHECK", width=6,
                                         command=lambda u=p["username"]: self.check(u))
                        btn.pack(side="right")
                        if self.master.seer_used:
                            btn.state(["disabled"])
                    elif role == "Doctor":
                        ttk.Button(p_frame, text="PROTECT", width=8,
                                   command=lambda u=p["username"]: self.protect(u)).pack(side="right")

    def update_phase(self, packet):
        phase_name = packet["phase"].upper() + " PHASE"
        self.phase_label.config(text=phase_name)
        if packet.get("msg"):
            self.add_system_msg(packet["msg"])
        # Show/hide vote tally panel
        if packet["phase"] == "voting":
            self.vote_frame.pack(fill="x", pady=(15, 0))
            self.vote_text.config(text="No votes yet.")
        else:
            self.vote_frame.pack_forget()
        self.update_players(self.master.players)

    def update_timer(self, seconds):
        sec_int = int(seconds)
        self.timer_label.config(text=f"{sec_int}s")
        if self.master.timer_total > 0:
            self.timer_bar["value"] = (sec_int / self.master.timer_total) * 100
        self.timer_label.config(fg="#ff4d4d" if sec_int <= 5 else "#ffffff")
        self.timer_bar.update_idletasks()

    def update_ping(self, ping_str):
        self.ping_label.config(text=f"Ping: {ping_str}")

    def update_vote_counts(self, packet):
        votes_in = packet.get("votes_in", 0)
        total    = packet.get("total", 0)
        self.vote_text.config(text=f"{votes_in}/{total} voted")

    def set_role(self, role):
        self.master.role = role
        self.role_label.config(text=f"ROLE: {role.upper()}")

    def add_chat(self, packet):
        self._display_msg(f"[{packet.get('sender','???')}]: {packet.get('msg','')}")

    def add_seer_result(self, packet):
        self._display_msg(f"🔮 SEER VISION: {packet.get('target')} is a {packet.get('role','?')}!")

    def add_system_msg(self, msg):
        self._display_msg(f"✨ SYSTEM: {msg}")

    def _display_msg(self, text):
        self.chat_area.config(state="normal")
        self.chat_area.insert(tk.END, str(text) + "\n")
        self.chat_area.config(state="disabled")
        self.chat_area.see(tk.END)

    def send_chat(self):
        msg = self.msg_entry.get().strip()
        if msg:
            self.master.net.send({"type": "chat", "msg": msg})
            self.msg_entry.delete(0, tk.END)

    def vote(self, target):
        self.master.net.send({"type": "vote", "target": target})

    def kill(self, target):
        self.master.net.send({"type": "kill", "target": target})

    def check(self, target):
        self.master.net.send({"type": "check", "target": target})
        self.master.seer_used = True
        self.update_players(self.master.players)

    def protect(self, target):
        self.master.net.send({"type": "protect", "target": target})

    def show_hunter_dialog(self):
        if self._hunter_dialog and self._hunter_dialog.winfo_exists():
            return
        self._hunter_dialog = tk.Toplevel(self.master)
        self._hunter_dialog.title("Hunter's Last Shot")
        self._hunter_dialog.configure(bg="#1a1a2e")
        self._hunter_dialog.grab_set()

        tk.Label(self._hunter_dialog, text="You were eliminated!",
                 font=self.master.font_header, bg="#1a1a2e", fg="#e94560").pack(pady=(30, 5))
        tk.Label(self._hunter_dialog, text="Choose someone to shoot (20s):",
                 font=self.master.font_main, bg="#1a1a2e", fg="#e1e1e1").pack(pady=(0, 20))

        alive_others = [p["username"] for p in self.master.players
                        if p.get("alive") and p["username"] != self.master.username]

        for uname in alive_others:
            ttk.Button(self._hunter_dialog, text=uname, width=20,
                       command=lambda u=uname: self._fire_hunter_shot(u)).pack(pady=4)

        ttk.Button(self._hunter_dialog, text="Don't shoot", width=20,
                   command=self._hunter_dialog.destroy).pack(pady=(20, 30))

    def _fire_hunter_shot(self, target):
        self.master.net.send({"type": "hunter_shot", "target": target})
        if self._hunter_dialog and self._hunter_dialog.winfo_exists():
            self._hunter_dialog.destroy()


# ====================================================================== #
#  Game Over Frame                                                        #
# ====================================================================== #

class GameOverFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["ended"]["bg"])
        self.master = master
        results    = master.game_results or {}
        winner     = results.get("winner", "DRAW").upper()
        roles      = results.get("roles", {})
        win_color  = "#ffd700" if winner == "VILLAGER" else "#ff4d4d"

        tk.Label(self, text="GAME OVER", font=master.font_title,
                 bg=master["bg"], fg="#e1e1e1").pack(pady=(80, 20))
        tk.Label(self, text=f"{winner}S WIN!", font=("Helvetica", 40, "bold"),
                 bg=master["bg"], fg=win_color).pack(pady=20)

        roles_frame = tk.Frame(self, bg="#16213e", padx=40, pady=40,
                                highlightthickness=2, highlightbackground=win_color)
        roles_frame.pack(pady=40, padx=100, fill="both", expand=True)

        tk.Label(roles_frame, text="FINAL REVEAL:", font=master.font_header,
                 bg="#16213e", fg="#e1e1e1").pack(pady=(0, 20))

        scroll_frame = tk.Frame(roles_frame, bg="#16213e")
        scroll_frame.pack(fill="both", expand=True)
        canvas   = tk.Canvas(scroll_frame, bg="#16213e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        inner    = tk.Frame(canvas, bg="#16213e")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for user, role in roles.items():
            r_color = "#ff4d4d" if role == "Werewolf" else "#00d2ff"
            f = tk.Frame(inner, bg="#16213e", pady=5)
            f.pack(fill="x")
            tk.Label(f, text=user, font=master.font_bold, bg="#16213e",
                     fg="white", width=20, anchor="w").pack(side="left")
            tk.Label(f, text=f"➔  {role}", font=master.font_bold,
                     bg="#16213e", fg=r_color).pack(side="left")

        ttk.Button(self, text="RETURN TO LOBBY", width=25,
                   command=self.return_to_lobby).pack(pady=40)

    def return_to_lobby(self):
        self.master.phase = "lobby"
        self.master.show_room_lobby()


if __name__ == "__main__":
    app = WerewolfClient()
    app.mainloop()
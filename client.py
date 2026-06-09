import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import socket
import threading
import json
import queue
import time
import uuid
import os
import sys

# Configuration
HOST = "127.0.0.1"
PORT = 5000
CLIENT_PORT = 5005  # Fixed local port to prevent multiple instances on same device

# Color Themes
THEMES = {
    "lobby":  {"bg": "#1a1a2e", "panel": "#16213e", "accent": "#00d2ff", "text": "#e1e1e1"},
    "night":  {"bg": "#0f0c29", "panel": "#302b63", "accent": "#e94560", "text": "#ffffff"},
    "day":    {"bg": "#ece9e6", "panel": "#ffffff", "accent": "#243b55", "text": "#333333"},
    "voting": {"bg": "#434343", "panel": "#000000", "accent": "#ff4d4d", "text": "#ffffff"},
    "ended":  {"bg": "#1a1a2e", "panel": "#16213e", "accent": "#ffd700", "text": "#ffffff"}
}

def get_mac_address():
    """Retrieve the device's MAC address."""
    return ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff)
                     for ele in range(0, 8*6, 8)][::-1])

class NetClient:
    def __init__(self, host, port, packet_queue):
        self.host = host
        self.port = port
        self.packet_queue = packet_queue
        self.sock = None
        self.connected = False
        self.running = False
        self.mac = get_mac_address()

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Bind to a fixed local port to enforce single instance per device
            try:
                self.sock.bind(("", CLIENT_PORT))
            except socket.error:
                # Need to use a temporary root for messagebox if app isn't ready
                temp_root = tk.Tk()
                temp_root.withdraw()
                messagebox.showerror("Error", f"Another instance of the game is already running on this device (Port {CLIENT_PORT} is busy).")
                temp_root.destroy()
                return False

            self.sock.settimeout(5)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
            self.connected = True
            self.running = True
            threading.Thread(target=self._recv_loop, daemon=True).start()
            
            # Auto-identify to server immediately
            self.send({"type": "identify"})
            
            return True
        except Exception as e:
            # For general connection errors, we don't necessarily exit, but for bind errors we did above
            print(f"Connection error: {e}")
            return False

    def send(self, data):
        if self.connected:
            # Auto-include MAC in every packet for reliability, though login is primary
            data["mac"] = self.mac
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
                    if line:
                        try:
                            packet = json.loads(line)
                            self.packet_queue.put(packet)
                        except Exception:
                            pass
            except Exception:
                self.connected = False
                break
        self.running = False

    def disconnect(self):
        self.running = False
        if self.sock:
            self.sock.close()


class WerewolfClient(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw() # Hide until connection confirmed
        
        self.title("Werewolf: Azrael of the Night")
        self.geometry("1000x750")
        self.configure(bg="#1a1a2e")

        self.packet_queue = queue.Queue()
        self.net = NetClient(HOST, PORT, self.packet_queue)
        
        self.username = ""
        self.room_code = ""
        self.is_host = False
        self.players = []
        self.phase = "lobby"
        self.role = ""
        self.alive = True
        self.timer = 0
        self.timer_total = 60
        self.is_ready = False
        self.game_results = None

        self._load_session()

        self.current_frame = None
        self._setup_styles()
        
        # CRITICAL: Exit immediately if binding/connection fails
        if not self.net.connect():
            self.destroy()
            sys.exit(1)
        
        self.deiconify() # Show window
        self.show_main_menu()
        
        self.after(100, self._process_packets)

    def _save_session(self):
        try:
            with open(".last_session.json", "w") as f:
                json.dump({"room": self.room_code, "user": self.username}, f)
        except:
            pass

    def _load_session(self):
        if os.path.exists(".last_session.json"):
            try:
                with open(".last_session.json", "r") as f:
                    data = json.load(f)
                    self.room_code = data.get("room", "")
                    self.username = data.get("user", "")
            except:
                pass

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        self.font_title = ("Helvetica", 32, "bold")
        self.font_header = ("Helvetica", 20, "bold")
        self.font_main = ("Helvetica", 12)
        self.font_bold = ("Helvetica", 12, "bold")
        self.font_chat = ("Courier", 11)
        self.font_timer = ("Helvetica", 18, "bold")

        style.configure("TFrame", background="#1a1a2e")
        style.configure("TLabel", background="#1a1a2e", foreground="#e1e1e1", font=self.font_main)
        style.configure("TButton", font=self.font_bold, padding=10)
        
        style.configure("Timer.Horizontal.TProgressbar", 
                        thickness=15, 
                        troughcolor="#16213e", 
                        background="#00d2ff",
                        bordercolor="#16213e",
                        lightcolor="#00d2ff",
                        darkcolor="#00d2ff")

    def update_theme(self, phase):
        theme = THEMES.get(phase, THEMES["lobby"])
        self.configure(bg=theme["bg"])
        if self.current_frame:
            self.current_frame.configure(bg=theme["bg"])
            if hasattr(self.current_frame, "apply_theme"):
                self.current_frame.apply_theme(theme)

    def switch_frame(self, frame_class):
        if self.current_frame:
            self.current_frame.destroy()
        self.current_frame = frame_class(self)
        self.current_frame.pack(fill="both", expand=True)
        self.update_theme(self.phase)

    def _process_packets(self):
        while not self.packet_queue.empty():
            packet = self.packet_queue.get()
            self._handle_packet(packet)
        self.after(100, self._process_packets)

    def _handle_packet(self, packet):
        ptype = packet.get("type")
        if ptype == "login_ok":
            self.username = packet["username"]
            self._save_session()
            if packet.get("reconnect"):
                # Reconnection detected!
                self.show_game_screen()
            else:
                self.show_lobby()
        elif ptype == "room_joined":
            self.room_code = packet["room"]
            self._save_session()
            self.is_host = packet.get("host", False)
            self.players = packet.get("players", [])
            self.show_username_selection()
        elif ptype == "players_list":
            self.players = packet.get("players", [])
            self.phase = packet.get("phase", "lobby")
            if hasattr(self.current_frame, "update_players"):
                self.current_frame.update_players(self.players)
        elif ptype == "phase_change":
            self.phase = packet["phase"]
            self.timer_total = packet.get("duration", 60)
            if self.phase not in ["lobby", "ended"]:
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
        elif ptype == "seer_result":
            if hasattr(self.current_frame, "add_seer_result"):
                self.current_frame.add_seer_result(packet)
        elif ptype == "game_over":
            self.game_results = packet
            self.phase = "ended"
            self.show_game_over()
        elif ptype == "error":
            messagebox.showerror("Error", packet.get("msg", "Unknown error"))
        elif ptype == "left_room":
            self.room_code = ""
            self.phase = "lobby"
            self.show_main_menu()

    def show_main_menu(self):
        self.phase = "lobby"
        self.switch_frame(MainMenuFrame)

    def show_username_selection(self):
        self.switch_frame(UsernameFrame)

    def show_lobby(self):
        self.phase = "lobby"
        self.switch_frame(LobbyFrame)

    def show_game_screen(self):
        self.switch_frame(GameFrame)

    def show_game_over(self):
        self.switch_frame(GameOverFrame)


class MainMenuFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        tk.Label(self, text="WEREWOLF", font=self.master.font_title, bg=self.master["bg"], fg="#00d2ff").pack(pady=(150, 10))
        tk.Label(self, text="Azrael of the Night", font=self.master.font_main, bg=self.master["bg"], fg="#e1e1e1").pack(pady=(0, 60))

        tk.Label(self, text="Enter Room Code to Join:", font=self.master.font_main, bg=self.master["bg"], fg="#e1e1e1").pack(pady=5)
        self.room_entry = ttk.Entry(self, font=self.master.font_header, width=15, justify="center")
        self.room_entry.pack(pady=10)
        
        # Pre-fill room code if available
        if self.master.room_code:
            self.room_entry.insert(0, self.master.room_code)

        btn_frame = tk.Frame(self, bg=self.master["bg"])
        btn_frame.pack(pady=30)

        ttk.Button(btn_frame, text="Join Room", width=18, command=self.join_room).pack(side="left", padx=15)
        ttk.Button(btn_frame, text="Create New Room", width=18, command=self.create_room).pack(side="left", padx=15)

    def join_room(self):
        code = self.room_entry.get().strip().upper()
        if not code:
            messagebox.showwarning("Warning", "Please enter a room code")
            return
        if not self.master.net.connected:
            if not self.master.net.connect():
                messagebox.showerror("Error", "Could not connect to server")
                return
        self.master.net.send({"type": "join", "room": code})

    def create_room(self):
        if not self.master.net.connected:
            if not self.master.net.connect():
                messagebox.showerror("Error", "Could not connect to server")
                return
        self.master.net.send({"type": "create", "room": "AUTO"})


class UsernameFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        tk.Label(self, text="WHO ARE YOU?", font=self.master.font_header, bg=self.master["bg"], fg="#00d2ff").pack(pady=(150, 30))
        tk.Label(self, text=f"Joining Room: {self.master.room_code}", font=self.master.font_main, bg=self.master["bg"], fg="#e1e1e1").pack(pady=(0, 30))

        self.user_entry = ttk.Entry(self, font=self.master.font_header, width=20, justify="center")
        self.user_entry.pack(pady=10)
        
        # Pre-fill username if available
        if self.master.username:
            self.user_entry.insert(0, self.master.username)
        
        self.user_entry.focus_set()

        ttk.Button(self, text="Enter the Night", width=20, command=self.confirm_username).pack(pady=40)

    def confirm_username(self):
        username = self.user_entry.get().strip()
        if not username:
            messagebox.showwarning("Warning", "Username cannot be empty")
            return
        self.master.net.send({"type": "login", "username": username})


class LobbyFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        self.header = tk.Frame(self, bg=self.master["bg"])
        self.header.pack(fill="x", pady=40, padx=60)

        self.room_label = tk.Label(self.header, text=f"ROOM: {self.master.room_code}", font=self.master.font_header, bg=self.master["bg"], fg="#00d2ff")
        self.room_label.pack(side="left")
        
        self.count_label = tk.Label(self.header, text="0 / 4 minimum players", font=self.master.font_main, bg=self.master["bg"], fg="#e1e1e1")
        self.count_label.pack(side="right")

        self.list_frame = tk.Frame(self, bg="#16213e", padx=30, pady=30, highlightthickness=2, highlightbackground="#00d2ff")
        self.list_frame.pack(fill="both", expand=True, padx=60, pady=10)

        tk.Label(self.list_frame, text="SURVIVORS IN LOBBY:", font=self.master.font_bold, bg="#16213e", fg="#e1e1e1").pack(anchor="w", pady=(0, 15))

        self.player_listbox = tk.Listbox(self.list_frame, font=self.master.font_bold, bg="#16213e", fg="white", 
                                         borderwidth=0, highlightthickness=0, selectbackground="#16213e")
        self.player_listbox.pack(fill="both", expand=True)

        self.footer = tk.Frame(self, bg=self.master["bg"])
        self.footer.pack(fill="x", pady=40, padx=60)

        self.ready_btn = ttk.Button(self.footer, text="Ready", width=15, command=self.toggle_ready)
        self.ready_btn.pack(side="left", padx=10)

        if self.master.is_host:
            self.start_btn = ttk.Button(self.footer, text="Start Game", width=15, command=self.start_game)
            self.start_btn.pack(side="left", padx=10)

        ttk.Button(self.footer, text="Leave Room", width=15, command=self.leave_room).pack(side="right", padx=10)
        
        self.update_players(self.master.players)

    def update_players(self, players):
        self.player_listbox.delete(0, tk.END)
        for p in players:
            ready_status = "✅ [READY]" if p.get("ready", False) else "❌ [NOT READY]"
            if p["username"] == self.master.username:
                self.master.is_ready = p.get("ready", False)
                self.ready_btn.config(text="UNREADY" if self.master.is_ready else "READY")
            
            host_tag = " 👑" if p.get("host") else ""
            self.player_listbox.insert(tk.END, f" {p['username']}{host_tag} ".ljust(30) + f"{ready_status}")
        
        count = len(players)
        self.count_label.config(text=f"{count} / 4 minimum players")

    def toggle_ready(self):
        self.master.net.send({"type": "ready", "status": not self.master.is_ready})

    def start_game(self):
        self.master.net.send({"type": "start"})

    def leave_room(self):
        self.master.net.send({"type": "leave"})


class GameFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["lobby"]["bg"])
        self.master = master

        self.top_bar = tk.Frame(self, bg="#16213e", height=100)
        self.top_bar.pack(fill="x")
        self.top_bar.pack_propagate(False)

        self.info_top = tk.Frame(self.top_bar, bg="#16213e")
        self.info_top.pack(fill="x", padx=30, pady=(15, 0))

        self.phase_label = tk.Label(self.info_top, text="NIGHT PHASE", font=self.master.font_header, bg="#16213e", fg="#e94560")
        self.phase_label.pack(side="left")

        # Timer moved here to be beside Phase Title
        self.timer_label = tk.Label(self.info_top, text="0s", font=self.master.font_timer, bg="#16213e", fg="#ffffff", padx=20)
        self.timer_label.pack(side="left")

        self.role_label = tk.Label(self.info_top, text=f"ROLE: {self.master.role.upper()}", font=self.master.font_header, bg="#16213e", fg="#00d2ff")
        self.role_label.pack(side="right")

        self.timer_frame = tk.Frame(self.top_bar, bg="#16213e")
        self.timer_frame.pack(fill="x", padx=30, pady=(5, 15))

        self.timer_bar = ttk.Progressbar(self.timer_frame, orient="horizontal", mode="determinate", maximum=100, style="Timer.Horizontal.TProgressbar")
        self.timer_bar.pack(fill="x", expand=True)

        self.content = tk.Frame(self, bg=self.master["bg"])
        self.content.pack(fill="both", expand=True)

        self.chat_frame = tk.Frame(self.content, bg=self.master["bg"], padx=20, pady=20)
        self.chat_frame.pack(side="left", fill="both", expand=True)

        tk.Label(self.chat_frame, text="REALTIME CHAT", font=self.master.font_bold, bg=self.master["bg"], fg="#e1e1e1").pack(anchor="w", pady=(0, 10))

        self.chat_area = scrolledtext.ScrolledText(self.chat_frame, bg="#16213e", fg="white", font=self.master.font_chat, 
                                                   state="disabled", borderwidth=0, highlightthickness=1, highlightbackground="#333")
        self.chat_area.pack(fill="both", expand=True)

        self.input_frame = tk.Frame(self.chat_frame, bg=self.master["bg"])
        self.input_frame.pack(fill="x", pady=(15, 0))

        self.msg_entry = ttk.Entry(self.input_frame, font=self.master.font_main)
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.msg_entry.bind("<Return>", lambda e: self.send_chat())

        self.send_btn = ttk.Button(self.input_frame, text="Send", width=10, command=self.send_chat)
        self.send_btn.pack(side="right")

        self.right_frame = tk.Frame(self.content, bg=self.master["bg"], width=350, padx=20, pady=20)
        self.right_frame.pack(side="right", fill="y")
        self.right_frame.pack_propagate(False)

        tk.Label(self.right_frame, text="PLAYER STATUS", font=self.master.font_bold, bg=self.master["bg"], fg="#e1e1e1").pack(pady=(0, 15))

        self.players_container = tk.Frame(self.right_frame, bg=self.master["bg"])
        self.players_container.pack(fill="both", expand=True)

        self.update_players(self.master.players)
        self.apply_theme(THEMES.get(self.master.phase, THEMES["lobby"]))

    def apply_theme(self, theme):
        self.configure(bg=theme["bg"])
        self.content.configure(bg=theme["bg"])
        self.chat_frame.configure(bg=theme["bg"])
        self.input_frame.configure(bg=theme["bg"])
        self.right_frame.configure(bg=theme["bg"])
        self.players_container.configure(bg=theme["bg"])
        for container in [self.chat_frame, self.right_frame]:
            for widget in container.winfo_children():
                if isinstance(widget, tk.Label):
                    widget.configure(bg=theme["bg"], fg=theme["text"])
        self.top_bar.configure(bg=theme["panel"])
        self.info_top.configure(bg=theme["panel"])
        self.timer_frame.configure(bg=theme["panel"])
        for widget in self.info_top.winfo_children():
            widget.configure(bg=theme["panel"])
        self.timer_label.configure(bg=theme["panel"])
        style = ttk.Style()
        style.configure("Timer.Horizontal.TProgressbar", background=theme["accent"], troughcolor=theme["panel"])
        if self.master.phase == "day":
            self.phase_label.configure(fg="#243b55")
            self.chat_area.configure(bg="#ffffff", fg="#333333", highlightbackground="#cccccc")
        else:
            self.phase_label.configure(fg=theme["accent"])
            self.chat_area.configure(bg="#16213e", fg="#ffffff", highlightbackground="#444444")

    def update_players(self, players):
        for widget in self.players_container.winfo_children():
            widget.destroy()
        theme = THEMES.get(self.master.phase, THEMES["lobby"])
        for p in players:
            p_frame = tk.Frame(self.players_container, bg=theme["panel"], pady=10, padx=12, 
                               highlightthickness=1, highlightbackground="#444")
            p_frame.pack(fill="x", pady=4)
            alive = p.get("alive", True)
            connected = p.get("connected", True)
            icon = "👤" if alive else "👻"
            conn_status = "" if connected else " [OFFLINE]"
            color = theme["text"] if (alive and connected) else "#888888"
            name = p['username']
            if p['username'] == self.master.username:
                name += " (YOU)"
                self.master.alive = alive
            tk.Label(p_frame, text=f"{icon} {name}{conn_status}", bg=theme["panel"], fg=color, font=self.master.font_bold).pack(side="left")
            if self.master.alive and alive and connected and p['username'] != self.master.username:
                if self.master.phase == "voting":
                    ttk.Button(p_frame, text="VOTE", width=6, command=lambda u=p['username']: self.vote(u)).pack(side="right")
                elif self.master.phase == "night":
                    if self.master.role == "Werewolf":
                        ttk.Button(p_frame, text="KILL", width=6, command=lambda u=p['username']: self.kill(u)).pack(side="right")
                    elif self.master.role == "Seer":
                        ttk.Button(p_frame, text="CHECK", width=6, command=lambda u=p['username']: self.check(u)).pack(side="right")

    def update_phase(self, packet):
        phase_name = packet["phase"].upper() + " PHASE"
        self.phase_label.config(text=phase_name)
        if packet.get("msg"):
            self.add_system_msg(packet["msg"])
        self.update_players(self.master.players)

    def update_timer(self, seconds):
        sec_int = int(seconds)
        self.timer_label.config(text=f"{sec_int}s")
        if self.master.timer_total > 0:
            progress = (sec_int / self.master.timer_total) * 100
            self.timer_bar["value"] = progress
        if sec_int <= 5:
            self.timer_label.config(fg="#ff4d4d")
        else:
            self.timer_label.config(fg="#ffffff")
        self.timer_bar.update_idletasks()

    def set_role(self, role):
        self.master.role = role
        self.role_label.config(text=f"ROLE: {role.upper()}")

    def add_chat(self, packet):
        sender = packet.get("sender", "???")
        msg = packet.get("msg", "")
        self._display_msg(f"[{sender}]: {msg}")

    def add_seer_result(self, packet):
        target = packet.get("target")
        role = packet.get("role", "Unknown")
        msg = f"🔮 SEER VISION: {target} is a {role}!"
        self._display_msg(msg)

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


class GameOverFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=THEMES["ended"]["bg"])
        self.master = master
        results = self.master.game_results
        winner = results.get("winner", "DRAW").upper()
        roles = results.get("roles", {})
        win_color = "#ffd700" if winner == "VILLAGER" else "#ff4d4d"
        tk.Label(self, text="GAME OVER", font=self.master.font_title, bg=self.master["bg"], fg="#e1e1e1").pack(pady=(80, 20))
        tk.Label(self, text=f"{winner}S WIN!", font=("Helvetica", 40, "bold"), bg=self.master["bg"], fg=win_color).pack(pady=20)
        roles_frame = tk.Frame(self, bg="#16213e", padx=40, pady=40, highlightthickness=2, highlightbackground=win_color)
        roles_frame.pack(pady=40, padx=100, fill="both", expand=True)
        tk.Label(roles_frame, text="FINAL REVEAL:", font=self.master.font_header, bg="#16213e", fg="#e1e1e1").pack(pady=(0, 20))
        scroll_frame = tk.Frame(roles_frame, bg="#16213e")
        scroll_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_frame, bg="#16213e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        scrollable_content = tk.Frame(canvas, bg="#16213e")
        scrollable_content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for user, role in roles.items():
            r_color = "#ff4d4d" if role == "Werewolf" else "#00d2ff"
            f = tk.Frame(scrollable_content, bg="#16213e", pady=5)
            f.pack(fill="x")
            tk.Label(f, text=f"{user}", font=self.master.font_bold, bg="#16213e", fg="white", width=20, anchor="w").pack(side="left")
            tk.Label(f, text=f"➔  {role}", font=self.master.font_bold, bg="#16213e", fg=r_color).pack(side="left")
        ttk.Button(self, text="RETURN TO LOBBY", width=25, command=self.return_to_lobby).pack(pady=40)

    def return_to_lobby(self):
        self.master.phase = "lobby"
        self.master.show_lobby()


if __name__ == "__main__":
    app = WerewolfClient()
    app.mainloop()

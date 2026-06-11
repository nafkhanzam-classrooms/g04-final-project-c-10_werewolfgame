from game.game_state import Role


class Player:
    def __init__(self, username, conn, addr):
        self.username  = username
        self.conn      = conn
        self.addr      = addr
        self.role      = Role.VILLAGER
        self.alive     = True
        self.ready     = False
        self.room      = None
        self.connected = True
        self.last_ping = None   # timestamp of last PING received (for watchdog)

    def __repr__(self):
        status = "ONLINE" if self.connected else "OFFLINE"
        return f"Player({self.username}, {self.role.value}, alive={self.alive}, {status})"

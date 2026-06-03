from game.game_state import Role

class Player:
    def __init__(self, username, conn, addr):
        self.username = username
        self.conn = conn
        self.addr = addr
        self.role = Role.VILLAGER
        self.alive = True
        self.room = None
        self.ping = 0
        self.connected = True

    def __repr__(self):
        return f"Player({self.username}, {self.role.value}, alive={self.alive})"
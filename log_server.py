import http.server
import os
import glob

LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
PORT = 5001

class LogHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # List all log files
        if self.path == "/logs":
            log_files = sorted(
                glob.glob(os.path.join(LOGS_DIR, "server_*.log")),
                key=os.path.getmtime, reverse=True
            )
            lines = ["Available log files:\n"]
            for f in log_files:
                fname = os.path.basename(f)
                size  = os.path.getsize(f)
                lines.append(f"  /logs/{fname}  ({size} bytes)\n")
            content = "".join(lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(content)

        # Fetch latest log
        elif self.path == "/logs/latest":
            self._serve_latest()

        # Fetch specific log file by name
        elif self.path.startswith("/logs/server_"):
            fname    = os.path.basename(self.path)
            filepath = os.path.join(LOGS_DIR, fname)
            if not os.path.exists(filepath):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Log file not found")
                return
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(content)

        else:
            self.send_response(404)
            self.end_headers()

    def _serve_latest(self):
        log_files = glob.glob(os.path.join(LOGS_DIR, "server_*.log"))
        if not log_files:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"No log files found")
            return
        latest = max(log_files, key=os.path.getmtime)
        with open(latest, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    with http.server.HTTPServer(("0.0.0.0", PORT), LogHandler) as httpd:
        print(f"Log server running on port {PORT}")
        httpd.serve_forever()
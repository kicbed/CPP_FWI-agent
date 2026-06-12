#!/usr/bin/env python3
"""
Lab Agent Workbench HTTP Server.

Serves the frontend and provides a simple HTTP server.
Usage: python3 web/serve.py [port]
Default port: 8080
"""

import http.server
import socketserver
import os
import sys
import webbrowser
import json
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
WEB_DIR = Path(__file__).parent.resolve()

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # CORS headers for API calls
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        # Quiet logging
        pass

def find_free_port(start=8080, max_tries=20):
    import socket
    for p in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', p))
                return p
            except OSError:
                continue
    return start

def main():
    port = find_free_port(PORT)

    with socketserver.TCPServer(("", port), Handler) as httpd:
        url = f"http://localhost:{port}"
        print(f"\033[1;36m┌─────────────────────────────────────────┐\033[0m")
        print(f"\033[1;36m│\033[0m  🌐 Lab Agent Workbench 已启动          \033[1;36m│\033[0m")
        print(f"\033[1;36m│\033[0m  📍 {url:<33} \033[1;36m│\033[0m")
        print(f"\033[1;36m│\033[0m  按 Ctrl+C 停止                         \033[1;36m│\033[0m")
        print(f"\033[1;36m└─────────────────────────────────────────┘\033[0m")

        try:
            webbrowser.open(url)
        except:
            pass

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\033[1;33mWeb UI 服务器已停止\033[0m")
            httpd.shutdown()

if __name__ == '__main__':
    main()

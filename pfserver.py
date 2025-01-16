#!/usr/bin/env python3

import os
import sys
import http.server
import argparse
import time
import subprocess
import json
from urllib.parse import unquote
from pathlib import Path

def load_config():
    """Load configuration from config file."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server_config.json')
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(f"Error: Configuration file not found at {config_path}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON in configuration file {config_path}")
        sys.exit(1)

# Load configuration
CONFIG = load_config()
DOC_ROOT = CONFIG['doc_root']
PORT_NUMBER = CONFIG['port']
EXCLUDED_FILES = CONFIG['excluded_files']
HEADER_IMAGE = CONFIG['header_image']
HEADER_TEXT = CONFIG['header_text']
HEADER_IMAGE_FILE = CONFIG['header_image_file']

PID_FILE = os.path.join(DOC_ROOT, 'server.pid')
LOG_FILE = os.path.join(DOC_ROOT, 'server.log')

class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        """Override to log to file instead of stderr."""
        with open(LOG_FILE, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {format%args}\n")

    def list_directory(self, path):
        """Override the method to list files in the directory and exclude some."""
        try:
            # Get files and filter out excluded ones
            files = [f for f in os.listdir(path) if f not in EXCLUDED_FILES]
            
            if not files:
                self.send_error(404, "No files found")
                return

            # Sort files case-insensitively
            files.sort(key=str.casefold)

            file_list_html = [
                '<html>',
                '<title>pfserver</title>',
                '<head>',
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
                '<link rel="icon" type="image/x-icon" href="favicon.ico">',
                '<link rel="stylesheet" type="text/css" href="style.css">',
                '</head>',
                '<body>'
            ]

            # Add header based on configuration
            if HEADER_IMAGE:
                file_list_html.append(f'<img src="{HEADER_IMAGE_FILE}"/>')
            else:
                file_list_html.append(f'<h1>{HEADER_TEXT}</h1>')

            file_list_html.extend([
                '<br>',
                '<h3>Click a file to download.</h3>',
                '<ul>'
            ])

            for filename in files:
                file_url = unquote(filename)
                file_list_html.append(f'<li><a href="{file_url}" download>{filename}</a></li>')

            file_list_html.extend(['</ul>', '</body>', '</html>'])
            html_content = '\n'.join(file_list_html)

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))

        except OSError:
            self.send_error(404, "No such directory")

    def do_GET(self):
        """Handle GET requests."""
        path = self.path.lstrip('/')
        if not path:
            path = ''

        full_path = os.path.join(DOC_ROOT, path)

        if os.path.exists(full_path):
            if os.path.isdir(full_path):
                self.list_directory(full_path)
            else:
                self.path = os.path.relpath(full_path, DOC_ROOT)
                super().do_GET()
        else:
            self.send_error(404, "File not found")

def daemonize():
    """Daemonize the process."""
    # First fork (detaches from parent)
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)  # Parent exit
    except OSError as err:
        sys.stderr.write(f'fork #1 failed: {err}\n')
        sys.exit(1)

    # Decouple from parent environment
    os.chdir(DOC_ROOT)
    os.umask(0)
    os.setsid()

    # Second fork (relinquish session leadership)
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)  # Parent exit
    except OSError as err:
        sys.stderr.write(f'fork #2 failed: {err}\n')
        sys.exit(1)

    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, 'r') as f:
        os.dup2(f.fileno(), sys.stdin.fileno())
    with open(LOG_FILE, 'a') as f:
        os.dup2(f.fileno(), sys.stdout.fileno())
        os.dup2(f.fileno(), sys.stderr.fileno())

def run_server():
    """Run the web server."""
    # Ensure the document root exists
    Path(DOC_ROOT).mkdir(parents=True, exist_ok=True)

    # Write PID file
    with open(PID_FILE, 'w') as pid_file:
        pid_file.write(str(os.getpid()))

    # Create and start the server
    server_address = ('', PORT_NUMBER)
    httpd = http.server.HTTPServer(server_address, CustomHTTPRequestHandler)
    
    # Log server start
    with open(LOG_FILE, 'a') as f:
        f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} - Server started on port {PORT_NUMBER}\n")
    
    httpd.serve_forever()

def stop_server():
    """Stop the server process."""
    if not Path(PID_FILE).exists():
        print("Server is not running.")
        return

    try:
        with open(PID_FILE, 'r') as pid_file:
            pid = int(pid_file.read().strip())

        # Try to terminate the process
        os.kill(pid, 15)  # Send SIGTERM
        
        # Wait for process to end (max 5 seconds)
        max_wait = 5
        while max_wait > 0:
            try:
                os.kill(pid, 0)  # Check if process exists
                time.sleep(1)
                max_wait -= 1
            except ProcessLookupError:
                break

        if max_wait == 0:
            print("Server shutdown timed out. Forcing termination...")
            try:
                os.kill(pid, 9)  # Send SIGKILL
            except ProcessLookupError:
                pass

        print("Server has been stopped.")

    except (ProcessLookupError, FileNotFoundError):
        print("Server process no longer exists.")
    except PermissionError:
        print("Permission denied while trying to terminate server process.")
    finally:
        if Path(PID_FILE).exists():
            os.remove(PID_FILE)

def server_status():
    """Check if server is running."""
    if not Path(PID_FILE).exists():
        return False

    try:
        with open(PID_FILE, 'r') as pid_file:
            pid = int(pid_file.read().strip())
        os.kill(pid, 0)  # Check if process exists
        return True
    except (ProcessLookupError, FileNotFoundError, ValueError):
        if Path(PID_FILE).exists():
            os.remove(PID_FILE)
        return False
    except PermissionError:
        return True

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Start or stop the file server.")
    parser.add_argument('-u', '--up', action='store_true', help="Start the web server")
    parser.add_argument('-d', '--down', action='store_true', help="Stop the web server")
    return parser.parse_args()

def main():
    """Main entry point."""
    args = parse_args()

    if args.up:
        if server_status():
            print("Server is already running.")
        else:
            print(f"Starting server on http://localhost:{PORT_NUMBER}.")
            # Fork the process
            try:
                daemonize()
                run_server()
            except OSError:
                # If fork is not available (e.g., on Windows), run in background
                print("Fork not available, running in background...")
                with open(os.devnull, 'w') as devnull:
                    subprocess.Popen([sys.executable, sys.argv[0], '--background'],
                                   stdout=devnull,
                                   stderr=devnull,
                                   close_fds=True)
            time.sleep(1)  # Give the server a moment to start
            if server_status():
                print(f"Server is running. Access it at http://localhost:{PORT_NUMBER}/")
                print(f"Server logs are available in {LOG_FILE}")
            else:
                print("Failed to start server. Check logs for details.")

    elif args.down:
        stop_server()
    
    elif '--background' in sys.argv:
        # Internal flag used when running in background mode
        run_server()
    
    else:
        print("Specify -u to start or -d to stop the server.")
        sys.exit(1)

if __name__ == '__main__':
    main()

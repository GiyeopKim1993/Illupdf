from __future__ import annotations
import argparse
import shlex
import subprocess
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from .consumer import consume_ndjson_stream
from .server import make_handler
from .state import VisualizerState


def run_visualizer_adapter(args: argparse.Namespace) -> int:
    command_string = " ".join(args.cmd) if args.cmd else "stdin"
    state = VisualizerState(command=command_string)
    handler = make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Visualizer: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        if args.stdin:
            consume_ndjson_stream(sys.stdin, state)
        elif args.cmd:
            actual_cmd: list[str] = []
            for token in args.cmd:
                if " " in token and not Path(token).exists():
                    actual_cmd.extend(shlex.split(token))
                else:
                    actual_cmd.append(token)

            cmd_to_run = [sys.executable, "-m", "illupdf"] + actual_cmd + ["--emit", "ndjson"]
            process = subprocess.Popen(
                cmd_to_run,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            consumer_thread = threading.Thread(
                target=consume_ndjson_stream,
                args=(process.stdout, state),
                daemon=True,
            )
            consumer_thread.start()
            process.wait()
            consumer_thread.join(timeout=1)
            if process.returncode != 0:
                error_msg = process.stderr.read()
                state.set_error(error_msg or f"process exited with code {process.returncode}")
        else:
            print("error: visualize command requires either --stdin or a command to run", file=sys.stderr)
            return 1

        input("Press Enter to close the visualizer...")
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        server.shutdown()
        server.server_close()
    return 0

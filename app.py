"""Launch the Auto Editor UI: python app.py [--port 8420] [--no-browser]"""

import subprocess
import sys
import threading
import webbrowser

from autoeditor.server import serve


def _check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            print(f"Error: {tool} not found on PATH. Install ffmpeg first.", file=sys.stderr)
            sys.exit(1)


USAGE = "usage: python app.py [--port 8420] [--no-browser]"


def main(argv: list[str]) -> int:
    if {"-h", "--help", "/?"} & set(argv):
        print(USAGE)
        return 0

    port = 8420
    open_browser = "--no-browser" not in argv
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            print(f"Error: --port needs a number.\n{USAGE}", file=sys.stderr)
            return 2

    _check_tools()

    httpd = serve(port=port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Auto Editor UI running at {url}")
    print("Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

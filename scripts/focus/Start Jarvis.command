#!/bin/zsh
# Double-click to start the Focus HUD. Keep this window open while you use it.
[ -f ~/.zshrc ] && source ~/.zshrc
export PATH="$HOME/.local/bin:$PATH"
export UV_HTTP_TIMEOUT=600
PORT=5173

echo "Checking for updates..."
if git -C "$HOME/.openjarvis/src" pull --ff-only --quiet; then
  echo "Up to date: version $(git -C "$HOME/.openjarvis/src" log -1 --format=%h)"
else
  echo "(Could not update; starting the version you already have.)"
fi

echo "Stopping any earlier copy..."
# Only the program serving the page, not browsers that are connected to it.
lsof -ti "tcp:$PORT" -sTCP:LISTEN | xargs kill -9 2>/dev/null
jarvis stop >/dev/null 2>&1

# After the old copy is stopped the system keeps its address reserved for a
# short while if a browser tab was still connected. Wait until it is free.
port_free() {
  /usr/bin/python3 -c "import socket; socket.socket().bind(('127.0.0.1', $PORT))" 2>/dev/null
}
if [ -x /usr/bin/python3 ]; then
  waited=0
  until port_free || [ $waited -ge 90 ]; do
    [ $waited -eq 0 ] && echo "Waiting for the old copy to let go (up to a minute)..."
    sleep 3
    waited=$((waited + 3))
  done
else
  sleep 45
fi

# Open the Focus page as soon as the screen is being served.
(
  until curl -s -o /dev/null "http://127.0.0.1:$PORT"; do sleep 2; done
  open "http://127.0.0.1:$PORT/focus"
) &
opener=$!

echo "Starting Jarvis. The Focus page opens by itself when it is ready."
jarvis gui --no-browser
kill $opener 2>/dev/null
echo
echo "Jarvis has stopped. Close this window and double-click Start Jarvis again."

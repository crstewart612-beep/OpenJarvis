#!/bin/zsh
# Double-click to start the Focus HUD. Keep this window open while you use it.
[ -f ~/.zshrc ] && source ~/.zshrc
export PATH="$HOME/.local/bin:$PATH"
export UV_HTTP_TIMEOUT=600

echo "Stopping any earlier copy..."
lsof -ti:5173 | xargs kill -9 2>/dev/null
jarvis stop >/dev/null 2>&1
sleep 2

# Open the Focus page as soon as the screen is being served.
(
  until curl -s -o /dev/null http://127.0.0.1:5173; do sleep 2; done
  open "http://127.0.0.1:5173/focus"
) &

echo "Starting Jarvis. The Focus page opens by itself when it is ready."
jarvis gui --no-browser
echo
echo "Jarvis has stopped. Close this window and double-click Start Jarvis again."

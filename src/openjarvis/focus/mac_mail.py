"""Unread mail, read live from the Mac's Mail app.

Nothing is written to disk: the subject lines exist only in the reply sent
to the screen and in a short in-memory cache. Mail is never changed.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from openjarvis.focus.mac_calendar import CalendarError, _osascript, available

__all__ = ["available", "read_unread", "MailError"]

MailError = CalendarError

_UNREAD = """
function run(argv) {
  const mail = Application('Mail');
  const limit = Number(argv[0]);
  const accounts = [];
  let total = 0;
  const latest = [];
  mail.accounts().forEach(acct => {
    let unread = 0;
    acct.mailboxes().forEach(box => {
      const name = box.name().toLowerCase();
      if (name !== 'inbox') return;
      unread += box.unreadCount();
      if (latest.length >= limit) return;
      const found = box.messages.whose({readStatus: false})();
      found.slice(0, limit - latest.length).forEach(m => latest.push({
        from: m.sender(), subject: m.subject(),
        date: m.dateReceived().toISOString(), account: acct.name(),
      }));
    });
    total += unread;
    accounts.push({name: acct.name(), unread: unread});
  });
  return JSON.stringify({unread: total, accounts: accounts, latest: latest});
}
"""


def read_unread(limit: int = 8) -> Dict[str, Any]:
    """Unread counts per account and the newest few unread subjects."""
    try:
        raw = _osascript(_UNREAD, str(limit), timeout=60.0)
    except CalendarError as exc:
        raise MailError(str(exc).replace("Calendar", "Mail")) from exc
    data = json.loads(raw or "{}")
    data.setdefault("unread", 0)
    data.setdefault("accounts", [])
    data.setdefault("latest", [])
    data["latest"].sort(key=lambda m: m.get("date", ""), reverse=True)
    return data

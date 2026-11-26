---
title: Rewind
emoji: 🔄
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
hf_oauth: true
hf_oauth_expiration_minutes: 43200
pinned: false
short_description: Bring back deleted photos and videos. Works in Claude.
---

# Rewind (cloud)

Finds deleted photos and videos wherever a copy still survives, and separates them from what you
still have. This is the cloud half of Rewind:

- **Accounts:** Google Drive (Trash), OneDrive (deleted items), Dropbox (deleted files it still
  keeps), Telegram (photos and videos still in your chats).
- **Uploads:** Google Takeout, Snapchat My Data, Instagram/Facebook downloads, WhatsApp chat
  exports, SMS backups, zipped iPhone backups, memory-card images, loose photos and videos.
- **Claude:** add `https://<this-space>.hf.space/mcp` in claude.ai › Settings › Connectors ›
  Add custom connector. You sign in with Hugging Face; then ask Claude to find and restore your
  deleted photos, on the web, the phone apps or Claude Desktop.

Only the Space owner can sign in (set `REWIND_ALLOWED_USERS` to allow other Hugging Face names).

It cannot see your Mac or phone: for those, run Rewind on the Mac, which also connects to
Claude Desktop and Claude Code (`python3 -m rewind claude-setup`).

To keep connected accounts and results across restarts, attach a storage bucket mounted at `/data`
(Space settings). Without it, the Space starts fresh after each restart.

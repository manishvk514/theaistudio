"""Live email: every photo or video ever attached to a mail you sent or got.

First principles: when you email a photo, the mail server stores its own
full copy, in your Sent folder and in the recipient's inbox. Deleting the
photo from your phone or Google Photos does not touch that copy. Years of
mail are often the biggest forgotten photo backup a person has.

Works with any mail service that offers IMAP (Gmail, Outlook/Hotmail,
iCloud Mail, Yahoo, Zoho, office mail). You sign in with an "app password"
created in your own account's security settings; it stays on this computer.
"""
import email
import email.header
import email.utils
import imaplib
import os
import re

from ..core import media

INFO = {
    "id": "email",
    "title": "Email (live mailbox: Gmail, Outlook, iCloud, Yahoo...)",
    "group": "Accounts",
    "blurb": "Searches every folder of your mailbox, including Sent and Trash, for photo and video "
             "attachments, and downloads them. Copies you emailed years ago survive deletion everywhere else.",
    "fields": [
        {"name": "provider", "label": "Mail service", "type": "choice", "choices": [
            ["gmail", "Gmail"], ["outlook", "Outlook / Hotmail / Live"], ["icloud", "iCloud Mail"],
            ["yahoo", "Yahoo"], ["other", "Other (type the server below)"]], "default": "gmail"},
        {"name": "address", "label": "Your email address", "type": "text"},
        {"name": "app_password", "label": "App password (Gmail, iCloud, Yahoo: made in your account's security "
                                         "settings, see guide)", "type": "password"},
        {"name": "client_id", "label": "Outlook/Hotmail only: the Microsoft app ID from the OneDrive setup "
                                       "(Outlook no longer accepts app passwords)", "type": "text"},
        {"name": "server", "label": "IMAP server (only for 'Other')", "type": "text"},
    ],
}

SERVERS = {"gmail": "imap.gmail.com", "outlook": "outlook.office365.com", "icloud": "imap.mail.me.com",
           "yahoo": "imap.mail.yahoo.com"}
MEDIA_RX = re.compile(rb'"(image|video)"|\.(jpe?g|png|heic|heif|gif|webp|mp4|mov|3gp|avi|mkv)"', re.I)


def _folders(m):
    typ, data = m.list()
    out = []
    for line in data or []:
        s = line.decode(errors="ignore")
        if "\\Noselect" in s:
            continue
        mm = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', s)
        name = mm.group(1) or mm.group(2)
        out.append((name, s))
    return out


def run(ctx, params):
    addr = (params.get("address") or "").strip()
    pw = (params.get("app_password") or "").replace(" ", "")
    host = SERVERS.get(params.get("provider"), (params.get("server") or "").strip())
    outlook = params.get("provider") == "outlook"
    if not (addr and host and (pw or (outlook and params.get("client_id")))):
        raise ValueError("Enter your email address and app password (or, for Outlook, the Microsoft app ID).")
    ctx.log("Connecting to %s ..." % host)
    m = imaplib.IMAP4_SSL(host, 993, timeout=60)
    try:
        if outlook and params.get("client_id"):
            from .cloud import ms_token
            tok = ms_token(ctx, params["client_id"].strip(),
                           scope="https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
                           key="ms_imap_token")
            m.authenticate("XOAUTH2", lambda _x: ("user=%s\x01auth=Bearer %s\x01\x01" % (addr, tok)).encode())
        else:
            m.login(addr, pw)
    except imaplib.IMAP4.error:
        raise ValueError("The mail service refused the sign-in. Check the address and that you used an "
                         "app password (not your normal password).")
    folders = _folders(m)
    gmail = host == "imap.gmail.com"
    if gmail:  # All Mail holds every message once; add Trash and Spam, which All Mail excludes
        keep = [f for f in folders if "\\All" in f[1] or "\\Trash" in f[1] or "\\Junk" in f[1]]
        folders = keep or folders
    out = ctx.out_dir("email")
    seen_ids = set()
    total = 0
    for fi, (name, flags) in enumerate(folders):
        if ctx.stopped():
            break
        try:
            typ, _ = m.select('"%s"' % name, readonly=True)
            if typ != "OK":
                continue
            if gmail:
                typ, data = m.search(None, "X-GM-RAW", '"has:attachment (filename:jpg OR filename:jpeg OR '
                                     'filename:png OR filename:heic OR filename:gif OR filename:webp OR '
                                     'filename:mp4 OR filename:mov OR filename:3gp)"')
            else:
                typ, data = m.search(None, "ALL")
        except Exception as e:
            ctx.log("Skipped folder %s: %s" % (name, e))
            continue
        uids = (data[0] or b"").split()
        ctx.log("%s: %d message(s) to check" % (name, len(uids)))
        trash = "\\Trash" in flags or "trash" in name.lower() or "deleted" in name.lower()
        for i in range(0, len(uids), 200):
            if ctx.stopped():
                break
            batch = uids[i:i + 200]
            ctx.progress(i, len(uids), name)
            if not gmail:  # cheap pre-filter on structure
                typ, st = m.fetch(b",".join(batch), "(BODYSTRUCTURE)")
                keep = []
                for x in st or []:
                    text = (x[0] + x[1]) if isinstance(x, tuple) else x
                    if isinstance(text, bytes) and text.strip() and MEDIA_RX.search(text):
                        keep.append(text.split()[0])
                batch = keep
            for uid in batch:
                typ, msgdata = m.fetch(uid, "(RFC822)")
                raw = next((x[1] for x in msgdata if isinstance(x, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                mid = msg.get("Message-ID") or uid
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                total += _save_parts(ctx, msg, out, name, trash)
    try:
        m.logout()
    except Exception:
        pass
    ctx.log("%d photo/video attachment(s) found in your mailbox." % total)


def _save_parts(ctx, msg, out, folder, trash):
    subj = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))[:80]
    who = msg.get("To", "") if "sent" in folder.lower() else msg.get("From", "")
    date = msg.get("Date")
    day = ""
    try:
        day = email.utils.parsedate_to_datetime(date).date().isoformat()
    except Exception:
        pass
    n = 0
    for part in msg.walk():
        ctype = part.get_content_type() or ""
        fn = part.get_filename() or ""
        if not (ctype.startswith(("image/", "video/")) or os.path.splitext(fn)[1].lower() in media.MEDIA_EXTS):
            continue
        data = part.get_payload(decode=True)
        if not data or len(data) < 8192:  # logos, signatures, tracking pixels
            continue
        if ctx.ingest_bytes(data, os.path.splitext(fn)[1].lower() or ".jpg", "trash" if trash else "export",
                            'Email › %s › "%s" (%s, %s)' % (folder, subj, str(who)[:60], day),
                            name=fn or "attachment", subdir="email",
                            evidence="Attached to an email; the mail server kept its own copy"):
            n += 1
    return n

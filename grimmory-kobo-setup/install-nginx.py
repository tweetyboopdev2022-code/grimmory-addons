#!/usr/bin/env python3
"""Patch Grimmory's nginx.conf for the Kobo setup page. Safe to run twice.

  python3 install-nginx.py /path/to/nginx.conf

Adds a /kobo-setup/ location serving the page and its files from the folder the proxy container
mounts at /usr/share/nginx/kobo-setup, and injects link.js (the box in Settings → Devices) into Grimmory's
pages before </head>. Edits the file in place (same inode, so the bind mount keeps working),
keeps a one-time .kobo-setup.bak copy, and refuses if it cannot find `location / {`.
"""
import re
import shutil
import sys
from pathlib import Path

MARK = "# grimmory-kobo-setup"
LOCATION = f"""    {MARK} begin
    location = /kobo-setup {{
        return 301 /kobo-setup/;
    }}
    location /kobo-setup/ {{
        alias /usr/share/nginx/kobo-setup/;
        index index.html;
        add_header Cache-Control "no-cache";
        types {{
            text/html html;
            text/css css;
            application/javascript js;
            application/json json;
            application/zip zip;
            application/octet-stream tgz cmd sh ini;
        }}
    }}
    {MARK} end
"""
SUB = "sub_filter '</head>' '<script src=\"/kobo-setup/link.js\" defer></script></head>';"


def inject_lines(block: str) -> str:
    """Only what the location does not already say: nginx allows these directives once each."""
    out = "        %s inject\n" % MARK
    if "Accept-Encoding" not in block:
        out += '        proxy_set_header Accept-Encoding "";\n'
    if "sub_filter_types" not in block:
        out += "        sub_filter_types text/html;\n"
    return out + "        " + SUB + "\n"


def block_after(text: str, start: int) -> str:
    """The body of the block whose opening brace was just before `start`."""
    depth = 1
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i]
    return text[start:]


def patch(text: str) -> str | None:
    """The patched config, or None if there is no `location / {` to hang it on."""
    m = re.search(r"^([ \t]*)location\s+/\s*\{[^\n]*\n", text, re.M)
    if not m:
        return None
    inject = inject_lines(block_after(text, m.end()))
    return text[:m.start()] + LOCATION + "\n" + text[m.start():m.end()] + inject + text[m.end():]


def main(path: str) -> int:
    p = Path(path)
    text = p.read_text()
    if MARK in text:
        print("already patched, nothing to do")
        return 0
    out = patch(text)
    if out is None:
        print("could not find a `location / {` block; add the snippet by hand (see README)")
        return 1
    bak = p.with_name(p.name + ".kobo-setup.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
    with open(p, "r+") as f:      # rewrite in place: keep the inode
        f.seek(0)
        f.write(out)
        f.truncate()
    print("patched %s (backup: %s)" % (p, bak.name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else print(__doc__) or 2)

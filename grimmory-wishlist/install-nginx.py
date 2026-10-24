#!/usr/bin/env python3
"""Patch Grimmory's nginx.conf for the wishlist. Safe to run twice.

  python3 install-nginx.py /path/to/nginx.conf

Adds two proxy locations and injects the script/stylesheet into Grimmory's HTML.
Edits the file in place (same inode, so the bind mount keeps working), keeps a
one-time .wishlist.bak copy, and refuses if it cannot find `location / {`.
"""
import re
import shutil
import sys
from pathlib import Path

MARK = "# grimmory-wishlist"
LOCATIONS = f"""    {MARK} begin
    location /wishlist-api/ {{
        proxy_pass http://wishlist:8080/;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Host $host;
    }}
    location /wishlist-static/ {{
        proxy_pass http://wishlist:8080/static/;
        add_header Cache-Control "no-cache";
    }}
    {MARK} end
"""
SUB = ("sub_filter '</body>' '<link rel=\"stylesheet\" href=\"/wishlist-static/wishlist.css\">"
       "<script src=\"/wishlist-static/wishlist.js\" defer></script></body>';")


def inject_lines(block: str) -> str:
    """Only what the location does not already say: nginx allows these directives once each."""
    out = "        %s inject\n" % MARK
    if "Accept-Encoding" not in block:
        out += '        proxy_set_header Accept-Encoding "";\n'
    if "sub_filter_once" not in block:
        out += "        sub_filter_once on;\n"
    return out + "        " + SUB + "\n"


def block_after(text: str, start: int) -> str:
    """The body of the block whose opening brace was just before `start`."""
    depth = 1
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i]
    return text[start:]


def main(path: str) -> int:
    p = Path(path)
    text = p.read_text()
    if MARK in text:
        print("already patched, nothing to do")
        return 0
    m = re.search(r"^([ \t]*)location\s+/\s*\{[^\n]*\n", text, re.M)
    if not m:
        print("could not find a `location / {` block; add the snippet by hand (see README)")
        return 1
    bak = p.with_name(p.name + ".wishlist.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
    inject = inject_lines(block_after(text, m.end()))
    out = text[:m.start()] + LOCATIONS + "\n" + text[m.start():m.end()] + inject + text[m.end():]
    with open(p, "r+") as f:      # rewrite in place: keep the inode
        f.seek(0)
        f.write(out)
        f.truncate()
    print("patched %s (backup: %s)" % (p, bak.name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else print(__doc__) or 2)

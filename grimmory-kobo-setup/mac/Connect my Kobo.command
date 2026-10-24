#!/bin/bash
# Connect my Kobo (Mac). Double-click with the Kobo plugged in.
#
# Signs in to the library, turns on Kobo sync for your account, points the Kobo at it and
# deletes macOS ._ leftovers. Every file it changes is copied first to
# .adds/grimmory-setup/backups/<time>/ on the Kobo, in the same layout the library's Kobo setup
# page uses, so that page's Undo can put any of it back.
#
# Uses only what every Mac has: bash, curl, awk and osascript (for JSON).

SERVER="${GRIMMORY_URL:-https://library.example.com}"   # build.py fills in your address
export COPYFILE_DISABLE=1          # never write ._ files of our own onto the Kobo

CONF_REL=".kobo/Kobo/Kobo eReader.conf"
BACKUPS_REL=".adds/grimmory-setup/backups"

bold=$(printf '\033[1m'); red=$(printf '\033[31m'); green=$(printf '\033[32m'); off=$(printf '\033[0m')
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$green" "$off" "$*"; }
bye()  { printf '\n'; read -r -p "Press Enter to close this window. " _; exit "${1:-0}"; }
die()  { printf '\n%s%s%s\n' "$red" "$*" "$off"; bye 1; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/connect-kobo.XXXXXX") || die "Could not make a temporary folder."
trap 'rm -rf "$WORK"' EXIT

# ---- JSON, through the JavaScript every Mac has --------------------------------------------
# Every argument goes in with an x in front, so a value starting with - is never read as an option.
js() {
  local code="$1"; shift; local args=(); for v in "$@"; do args+=("x$v"); done
  osascript -l JavaScript -e "function run(a){a=a.map(function(v){return v.slice(1)});$code}" "${args[@]}" 2>/dev/null
}
json_get() {      # json_get <file> <field>  -> value ('' if missing)
  js 'var o=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(a[0],4,null).js);var v=o==null?null:o[a[1]];return v==null?"":String(v)' "$1" "$2"
}
json_login() { js 'return JSON.stringify({username:a[0],password:a[1]})' "$1" "$2"; }
json_str() { js 'return JSON.stringify(a[0])' "$1"; }
json_enable() {   # settings file -> same settings with syncEnabled true
  js 'var o=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(a[0],4,null).js);o.syncEnabled=true;return JSON.stringify(o)' "$1"
}

# ---- Kobo eReader.conf: change one line, keep every other byte ----------------------------
# set_conf <in> <out> <value>; exit 0 changed (prints replaced/inserted), 3 unchanged,
# 4 no [OneStoreServices] section, 5 api_endpoint appears more than once.
set_conf() {
  local nl=1
  [ -s "$1" ] && [ "$(tail -c 1 "$1" | od -An -c | tr -d ' ')" != '\n' ] && nl=0
  awk -v val="$3" -v nl="$nl" -v out="$2" '
    function strip(s) { sub(/\r$/, "", s); return s }
    function secname(s,   t) { t = strip(s); if (t !~ /^[ \t]*\[.*\][ \t]*$/) return ""; sub(/^[ \t]*\[/, "", t); sub(/\][ \t]*$/, "", t); return (t == "" || t ~ /\]/) ? "" : t }
    { line[NR] = $0 }
    END {
      n = NR; start = 0; stop = n + 1
      for (i = 1; i <= n; i++) {
        s = secname(line[i]); if (s == "") continue
        if (start) { stop = i; break }
        if (s == "OneStoreServices") start = i
      }
      if (!start) exit 4
      hits = 0
      for (i = start + 1; i < stop; i++) if (line[i] ~ /^[ \t]*api_endpoint[ \t]*=/) { hits++; at = i }
      if (hits > 1) exit 5
      crs = 0; for (i = 1; i <= n; i++) if (line[i] ~ /\r$/) crs++
      total = n + (nl ? 1 : 0); cr = (crs > int(total / 2)) ? "\r" : ""
      what = ""
      if (hits == 1) {
        cur = line[at]; sub(/^[^=]*=/, "", cur); cur = strip(cur); gsub(/^[ \t]+|[ \t]+$/, "", cur)
        if (cur == val) exit 3
        line[at] = "api_endpoint=" val (line[at] ~ /\r$/ ? "\r" : ""); what = "replaced"
      } else {
        last = stop - 1
        while (last > start && strip(line[last]) ~ /^[ \t]*$/) last--
        for (i = n; i > last; i--) line[i + 1] = line[i]
        line[last + 1] = "api_endpoint=" val cr; n++; what = "inserted"
      }
      for (i = 1; i <= n; i++) printf "%s%s", line[i], (i < n || nl ? "\n" : "") > out
      print what
    }' "$1"
}

conf_value() {   # the api_endpoint under [OneStoreServices], or nothing
  awk '
    { sub(/\r$/, "") }
    /^[ \t]*\[.*\][ \t]*$/ { insec = ($0 ~ /^[ \t]*\[OneStoreServices\][ \t]*$/); next }
    insec && /^[ \t]*api_endpoint[ \t]*=/ { sub(/^[^=]*=[ \t]*/, ""); sub(/[ \t]+$/, ""); print; exit }' "$1"
}

is_appledouble() { [ "$(head -c 4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')" = "00051607" ]; }

# ---- 1. find the Kobo ------------------------------------------------------------------------
clear
say "${bold}Connect my Kobo${off}"
say "This sets up your Kobo to get books from the library: $SERVER"
say ""

VOLUMES="${KOBO_VOLUMES:-/Volumes}"
find_kobos() { KOBOS=(); for v in "$VOLUMES"/*; do [ -f "$v/.kobo/version" ] && KOBOS+=("$v"); done; }
find_kobos
if [ ${#KOBOS[@]} -eq 0 ]; then
  say "Plug in your Kobo with its USB cable and tap ${bold}Connect${off} on the Kobo."
  printf "Waiting for it to appear"
  while [ ${#KOBOS[@]} -eq 0 ]; do sleep 2; printf "."; find_kobos; done
  printf "\n"
fi
KOBO="${KOBOS[0]}"
if [ ${#KOBOS[@]} -gt 1 ]; then
  say "More than one Kobo is plugged in:"
  for i in "${!KOBOS[@]}"; do say "  $((i + 1))) ${KOBOS[$i]}"; done
  read -r -p "Which one? " pickn
  KOBO="${KOBOS[$((pickn - 1))]}"
  [ -n "$KOBO" ] && [ -f "$KOBO/.kobo/version" ] || die "That is not one of the numbers above."
fi
IFS=, read -r -a VER < "$KOBO/.kobo/version"
SERIAL="${VER[0]}"; FIRMWARE=""
for f in "${VER[@]:1}"; do f="${f%$'\r'}"; f="${f// /}"; if [[ "$f" =~ ^[0-9]+\.[0-9]+\.[0-9]{4,}$ ]]; then FIRMWARE="$f"; break; fi; done
ok "Found your Kobo at $KOBO (serial $SERIAL, firmware ${FIRMWARE:-unknown})"
[ -w "$KOBO/.kobo" ] || die "The Mac will not let this change files on the Kobo. Unplug it, plug it back in, tap Connect, and try again."
[ -f "$KOBO/$CONF_REL" ] || die "The Kobo has not finished its first start-up yet (no settings file). Finish setting it up on the Kobo, then try again."
# ---- 2. sign in and get the sync key ---------------------------------------------------------
say ""
say "Sign in with your ${bold}library${off} username and password."
ACCESS=""
for attempt in 1 2 3; do
  read -r -p "Username: " USERNAME
  read -r -s -p "Password: " PASSWORD; printf "\n"
  json_login "$USERNAME" "$PASSWORD" > "$WORK/login.json"
  code=$(curl -sS -o "$WORK/token.json" -w '%{http_code}' -H 'Content-Type: application/json' \
         --data-binary @"$WORK/login.json" "$SERVER/api/v1/auth/login") || die "Could not reach the library at $SERVER. Check this Mac is online."
  rm -f "$WORK/login.json"; PASSWORD=""
  if [ "$code" = "200" ]; then ACCESS=$(json_get "$WORK/token.json" accessToken); break; fi
  say "${red}That username and password did not work.${off} Try again."
done
[ -n "$ACCESS" ] || die "Could not sign in. Check your username and password in the library, then run this again."
ok "Signed in as $USERNAME"

auth=(-H "Authorization: Bearer $ACCESS")
code=$(curl -sS -o "$WORK/kobo.json" -w '%{http_code}' "${auth[@]}" "$SERVER/api/v1/kobo-settings")
[ "$code" = "403" ] && die "Your account is not allowed to sync a Kobo yet. Ask whoever runs the library to tick \"Sync Kobo\" on your account, then run this again."
[ "$code" = "200" ] || die "The library did not hand out a sync key (error $code)."
if [ "$(json_get "$WORK/kobo.json" syncEnabled)" != "true" ]; then
  json_enable "$WORK/kobo.json" > "$WORK/kobo-on.json"
  code=$(curl -sS -o "$WORK/kobo.json" -w '%{http_code}' -X PUT -H 'Content-Type: application/json' "${auth[@]}" \
         --data-binary @"$WORK/kobo-on.json" "$SERVER/api/v1/kobo-settings")
  [ "$code" = "200" ] || die "The library would not turn on Kobo sync (error $code)."
  ok "Turned on Kobo sync for your account"
fi
TOKEN=$(json_get "$WORK/kobo.json" token)
[[ "$TOKEN" =~ ^[A-Za-z0-9_-]{8,}$ ]] || die "The library returned a sync key that does not look right."
ENDPOINT="$SERVER/api/kobo/$TOKEN"

# ---- 3. plan ---------------------------------------------------------------------------------
CHANGES=(); WRITES=(); REMOVES=(); CONF_CHANGED=0
cp "$KOBO/$CONF_REL" "$WORK/conf.old"
set_conf "$WORK/conf.old" "$WORK/conf.new" "$ENDPOINT" > /dev/null; rc=$?
case $rc in
  0) CHANGES+=(sync); CONF_CHANGED=1 ;;
  3) ;;
  4) die "The Kobo's settings have no store section yet, so it has not finished setting up. Finish setting it up, then try again." ;;
  5) die "The Kobo's settings list the sync address twice; not guessing which to change. Nothing was changed." ;;
  *) die "Could not read the Kobo's settings file." ;;
esac
JUNK=()
while IFS= read -r -d '' f; do
  is_appledouble "$f" && JUNK+=("${f#"$KOBO"/}")
done < <(find "$KOBO" -path "$KOBO/$BACKUPS_REL" -prune -o -type f -name '._*' -print0 2>/dev/null)
[ ${#JUNK[@]} -gt 0 ] && { REMOVES+=("${JUNK[@]}"); CHANGES+=(mac-files); }
[ $CONF_CHANGED = 1 ] && WRITES+=("$CONF_REL|$WORK/conf.new")   # the settings file last

say ""
if [ ${#WRITES[@]} -eq 0 ] && [ ${#REMOVES[@]} -eq 0 ]; then
  ok "This Kobo is already set up. Nothing to change."
  bye 0
fi
say "${bold}This will:${off}"
for c in "${CHANGES[@]}"; do
  case $c in
    sync) say "  - connect the Kobo to the library as $USERNAME (now: $(conf_value "$WORK/conf.old" | sed -E 's#(/api/kobo/....).*#\1...#'))" ;;
    mac-files) say "  - delete ${#JUNK[@]} leftover Mac file(s) (._ files)" ;;
  esac
done
say "A copy of everything it changes is kept on the Kobo, so it can be undone from the library's Kobo setup page."
read -r -p "Press Enter to go ahead, or close this window to stop. " _

# ---- 4. back up, then change ----------------------------------------------------------------
STAMP=$(date +%Y%m%d-%H%M%S)
BK="$KOBO/$BACKUPS_REL/$STAMP"
mkdir -p "$BK/files" || die "Could not write the backup on the Kobo. Nothing was changed."
entries=""; kept=0
backup_one() {
  local rel="$1" existed=false
  if [ -f "$KOBO/$rel" ]; then
    mkdir -p "$BK/files/$(dirname "$rel")" && cat "$KOBO/$rel" > "$BK/files/$rel" || die "Could not back up $rel. Nothing else was changed."
    existed=true; kept=$((kept + 1))
  fi
  entries="$entries${entries:+,}
    {\"path\": \"$rel\", \"existed\": $existed}"
}
for w in "${WRITES[@]}"; do backup_one "${w%%|*}"; done
for r in "${REMOVES[@]}"; do backup_one "$r"; done
changes_json=$(printf '"%s",' "${CHANGES[@]}"); changes_json="[${changes_json%,}]"
cat > "$BK/manifest.json" <<EOF
{
  "version": 1,
  "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "tool": "mac",
  "user": $(json_str "$USERNAME"),
  "serial": "$SERIAL",
  "changes": $changes_json,
  "entries": [$entries
  ]
}
EOF
ok "Backed up $kept file(s) on the Kobo"

for w in "${WRITES[@]}"; do
  rel="${w%%|*}"; src="${w#*|}"; dst="$KOBO/$rel"
  mkdir -p "$(dirname "$dst")" || die "Could not make a folder for $rel."
  cat "$src" > "$dst.connect-tmp" && mv -f "$dst.connect-tmp" "$dst" || die "Could not write $rel. Undo is on the library's Kobo setup page."
  cmp -s "$src" "$dst" || die "After writing $rel the Kobo reads back something different."
  case "$rel" in
    "$CONF_REL") ok "Connected the Kobo to your account" ;;
  esac
done
for r in "${REMOVES[@]}"; do rm -f "$KOBO/$r"; done
[ ${#JUNK[@]} -gt 0 ] && ok "Deleted ${#JUNK[@]} leftover Mac file(s)"
[ "$(conf_value "$KOBO/$CONF_REL")" = "$ENDPOINT" ] || die "The Kobo's settings do not show the new sync address after writing."
# Writing to the Kobo can leave fresh ._ files of the backup folder; take those too.
find "$KOBO/.adds/grimmory-setup" -name '._*' -type f -delete 2>/dev/null

# ---- 5. done ---------------------------------------------------------------------------------
say ""
say "${bold}${green}Done.${off}"
read -r -p "Eject the Kobo now? (Y/n) " yn
case "$yn" in
  [nN]*) say "Eject it before unplugging: the eject arrow next to its name in Finder." ;;
  *) if diskutil eject "$KOBO" >/dev/null 2>&1; then ok "Ejected. You can unplug the Kobo."
     else say "The Mac could not eject it yet. Use the eject arrow next to its name in Finder."; fi ;;
esac
say ""
say "After unplugging: connect the Kobo to Wi-Fi and tap Sync. Books you put on your Kobo shelf in the library arrive on the Kobo."
bye 0

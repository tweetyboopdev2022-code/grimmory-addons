<# : Connect my Kobo (Windows). Double-click with the Kobo plugged in.
@echo off
title Connect my Kobo
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText('%~f0'); iex $s"
echo.
pause
exit /b
#>
# The lines above run this same file as PowerShell; everything below is PowerShell (5.1 or later).
#
# Signs in to the library, turns on Kobo sync for your account, points the Kobo at it and
# deletes macOS ._ leftovers. Every file it changes is copied first to
# .adds\grimmory-setup\backups\<time>\ on the Kobo, in the same layout the library's Kobo setup
# page uses, so that page's Undo can put any of it back.

$Server = if ($env:GRIMMORY_URL) { $env:GRIMMORY_URL } else { 'https://library.example.com' }   # build.py fills in your address
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$ConfRel = '.kobo/Kobo/Kobo eReader.conf'
$BackupsRel = '.adds/grimmory-setup/backups'
$Utf8 = New-Object System.Text.UTF8Encoding $false

function Say($t) { Write-Host $t }
function Ok($t) { Write-Host -NoNewline -ForegroundColor Green ([char]0x2713); Write-Host " $t" }
function Die($t) { Write-Host ''; Write-Host -ForegroundColor Red $t; throw 'stop' }
function KPath($rel) { Join-Path $script:Kobo ($rel -replace '/', [IO.Path]::DirectorySeparatorChar) }

# ---- Kobo eReader.conf: change one line, keep every other byte ---------------------------
function Set-ConfValue([string]$text, [string]$value) {
  $lines = $text.Split([char]10)
  $start = -1; $stop = $lines.Count
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $l = $lines[$i] -replace "`r$", ''
    if ($l -match '^\s*\[([^\]]+)\]\s*$') {
      if ($start -ge 0) { $stop = $i; break }
      if ($Matches[1] -eq 'OneStoreServices') { $start = $i }
    }
  }
  if ($start -lt 0) { return @{ what = 'nosection' } }
  $hits = @()
  for ($i = $start + 1; $i -lt $stop; $i++) { if ($lines[$i] -match '^\s*api_endpoint\s*=') { $hits += $i } }
  if ($hits.Count -gt 1) { return @{ what = 'duplicate' } }
  $crs = @($lines | Where-Object { $_ -match "`r$" }).Count
  $cr = if ($crs -gt [math]::Floor($lines.Count / 2)) { "`r" } else { '' }
  $list = New-Object System.Collections.Generic.List[string]
  $list.AddRange([string[]]$lines)
  if ($hits.Count -eq 1) {
    $i = $hits[0]
    $current = ($lines[$i] -split '=', 2)[1].Trim()
    if ($current -ceq $value) { return @{ what = 'unchanged'; text = $text } }
    $end = if ($lines[$i] -match "`r$") { "`r" } else { '' }
    $list[$i] = "api_endpoint=$value$end"
    return @{ what = 'replaced'; text = ($list -join "`n") }
  }
  $last = $stop - 1
  while ($last -gt $start -and -not $lines[$last].Trim()) { $last-- }
  $list.Insert($last + 1, "api_endpoint=$value$cr")
  return @{ what = 'inserted'; text = ($list -join "`n") }
}

function Get-ConfValue([string]$text) {
  $in = $false
  foreach ($l in ($text -split "`n")) {
    $l = $l -replace "`r$", ''
    if ($l -match '^\s*\[([^\]]+)\]\s*$') { $in = ($Matches[1] -eq 'OneStoreServices'); continue }
    if ($in -and $l -match '^\s*api_endpoint\s*=(.*)$') { return $Matches[1].Trim() }
  }
  return $null
}

function Same-Bytes($a, $b) {
  if ($null -eq $a -or $null -eq $b -or $a.Length -ne $b.Length) { return $false }
  for ($i = 0; $i -lt $a.Length; $i++) { if ($a[$i] -ne $b[$i]) { return $false } }
  return $true
}
function Read-KoboBytes($rel) { $p = KPath $rel; if (Test-Path -LiteralPath $p -PathType Leaf) { [IO.File]::ReadAllBytes($p) } else { $null } }
function Write-KoboBytes($rel, [byte[]]$data) {
  $p = KPath $rel
  $dir = Split-Path -Parent $p
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  [IO.File]::WriteAllBytes("$p.connect-tmp", $data)
  Move-Item -LiteralPath "$p.connect-tmp" -Destination $p -Force
}

function Call($method, $path, $json, $token) {
  $h = @{}
  if ($token) { $h['Authorization'] = "Bearer $token" }
  $args2 = @{ UseBasicParsing = $true; Method = $method; Uri = "$Server$path"; Headers = $h }
  if ($null -ne $json) { $args2['ContentType'] = 'application/json'; $args2['Body'] = $Utf8.GetBytes($json) }
  try {
    $r = Invoke-WebRequest @args2
    $body = $r.Content
    if ($body -is [byte[]]) { $body = $Utf8.GetString($body) }
    return @{ status = [int]$r.StatusCode; body = $body }
  } catch {
    $resp = $_.Exception.Response
    if ($resp) { return @{ status = [int]$resp.StatusCode; body = $null } }
    Die "Could not reach the library at $Server. Check this computer is online."
  }
}

function Main {
  Clear-Host
  Say 'Connect my Kobo'
  Say "This sets up your Kobo to get books from the library: $Server"
  Say ''

  # ---- 1. find the Kobo -----------------------------------------------------------------------
  function Find-Kobos {
    if ($env:KOBO_VOLUMES) { $roots = @(Get-ChildItem -LiteralPath $env:KOBO_VOLUMES -Directory | ForEach-Object { $_.FullName }) }
    else { $roots = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root }) }
    @($roots | Where-Object { Test-Path -LiteralPath (Join-Path $_ '.kobo/version') -PathType Leaf })
  }
  $kobos = @(Find-Kobos)
  if ($kobos.Count -eq 0) {
    Say 'Plug in your Kobo with its USB cable and tap Connect on the Kobo.'
    Write-Host -NoNewline 'Waiting for it to appear'
    while ($kobos.Count -eq 0) { Start-Sleep -Seconds 2; Write-Host -NoNewline '.'; $kobos = @(Find-Kobos) }
    Say ''
  }
  $script:Kobo = $kobos[0]
  if ($kobos.Count -gt 1) {
    Say 'More than one Kobo is plugged in:'
    for ($i = 0; $i -lt $kobos.Count; $i++) { Say "  $($i + 1)) $($kobos[$i])" }
    $n = 0; [void][int]::TryParse((Read-Host 'Which one?'), [ref]$n)
    if ($n -lt 1 -or $n -gt $kobos.Count) { Die 'That is not one of the numbers above.' }
    $script:Kobo = $kobos[$n - 1]
  }
  $ver = ([IO.File]::ReadAllText((KPath '.kobo/version'))).Trim() -split ','
  $serial = $ver[0].Trim(); $firmware = ''
  foreach ($f in ($ver | Select-Object -Skip 1)) { if ($f.Trim() -match '^\d+\.\d+\.\d{4,}$') { $firmware = $f.Trim(); break } }
  Ok "Found your Kobo at $script:Kobo (serial $serial, firmware $(if ($firmware) { $firmware } else { 'unknown' }))"
  if (-not (Test-Path -LiteralPath (KPath $ConfRel) -PathType Leaf)) { Die 'The Kobo has not finished its first start-up yet (no settings file). Finish setting it up on the Kobo, then try again.' }

  # ---- 2. sign in and get the sync key --------------------------------------------------------
  Say ''
  Say 'Sign in with your library username and password.'
  $access = $null
  for ($try = 1; $try -le 3 -and -not $access; $try++) {
    $user = Read-Host 'Username'
    if ([Console]::IsInputRedirected) { $pass = Read-Host 'Password' }      # piped in, nothing to hide
    else { $pass = (New-Object System.Net.NetworkCredential('', (Read-Host 'Password' -AsSecureString))).Password }
    $r = Call 'POST' '/api/v1/auth/login' (@{ username = $user; password = $pass } | ConvertTo-Json -Compress) $null
    $pass = $null
    if ($r.status -eq 200) { $access = ($r.body | ConvertFrom-Json).accessToken }
    else { Write-Host -ForegroundColor Red 'That username and password did not work. Try again.' }
  }
  if (-not $access) { Die 'Could not sign in. Check your username and password in the library, then run this again.' }
  Ok "Signed in as $user"

  $r = Call 'GET' '/api/v1/kobo-settings' $null $access
  if ($r.status -eq 403) { Die 'Your account is not allowed to sync a Kobo yet. Ask whoever runs the library to tick "Sync Kobo" on your account, then run this again.' }
  if ($r.status -ne 200) { Die "The library did not hand out a sync key (error $($r.status))." }
  $settings = $r.body | ConvertFrom-Json
  if (-not $settings.syncEnabled) {
    $settings.syncEnabled = $true
    $r = Call 'PUT' '/api/v1/kobo-settings' ($settings | ConvertTo-Json -Compress -Depth 5) $access
    if ($r.status -ne 200) { Die "The library would not turn on Kobo sync (error $($r.status))." }
    $settings = $r.body | ConvertFrom-Json
    Ok 'Turned on Kobo sync for your account'
  }
  if ($settings.token -notmatch '^[A-Za-z0-9_-]{8,}$') { Die 'The library returned a sync key that does not look right.' }
  $endpoint = "$Server/api/kobo/$($settings.token)"

  # ---- 3. plan --------------------------------------------------------------------------------
  $changes = @(); $writes = @(); $removes = @()
  $confOld = $Utf8.GetString([IO.File]::ReadAllBytes((KPath $ConfRel)))
  $res = Set-ConfValue $confOld $endpoint
  switch ($res.what) {
    'nosection' { Die "The Kobo's settings have no store section yet, so it has not finished setting up. Finish setting it up, then try again." }
    'duplicate' { Die "The Kobo's settings list the sync address twice; not guessing which to change. Nothing was changed." }
  }
  $junk = @()
  $backupsDir = KPath $BackupsRel
  foreach ($f in (Get-ChildItem -LiteralPath $script:Kobo -Recurse -Force -File -Filter '._*' -ErrorAction SilentlyContinue)) {
    if ($f.FullName.StartsWith($backupsDir)) { continue }
    $fs = [IO.File]::OpenRead($f.FullName); $head = New-Object byte[] 4; $n = $fs.Read($head, 0, 4); $fs.Close()
    if ($n -eq 4 -and $head[0] -eq 0 -and $head[1] -eq 5 -and $head[2] -eq 0x16 -and $head[3] -eq 7) {
      $junk += ($f.FullName.Substring($script:Kobo.TrimEnd('\', '/').Length + 1) -replace '\\', '/')
    }
  }
  if ($junk.Count) { $removes += $junk; $changes += 'mac-files' }
  if ($res.what -ne 'unchanged') { $changes = @('sync') + $changes; $writes += , @($ConfRel, $Utf8.GetBytes($res.text)) }   # the settings file last

  Say ''
  if ($writes.Count -eq 0 -and $removes.Count -eq 0) { Ok 'This Kobo is already set up. Nothing to change.'; return }
  Say 'This will:'
  foreach ($c in $changes) {
    switch ($c) {
      'sync' { Say "  - connect the Kobo to the library as $user" }
      'mac-files' { Say "  - delete $($junk.Count) leftover Mac file(s) (._ files)" }
    }
  }
  Say "A copy of everything it changes is kept on the Kobo, so it can be undone from the library's Kobo setup page."
  [void](Read-Host 'Press Enter to go ahead, or close this window to stop')

  # ---- 4. back up, then change ----------------------------------------------------------------
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $bk = "$BackupsRel/$stamp"
  $entries = @(); $kept = 0
  foreach ($rel in (@($writes | ForEach-Object { $_[0] }) + $removes)) {
    $old = Read-KoboBytes $rel
    if ($null -ne $old) { Write-KoboBytes "$bk/files/$rel" $old; $kept++ }
    $entries += [ordered]@{ path = $rel; existed = ($null -ne $old) }
  }
  $manifest = [ordered]@{
    version = 1; created = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); tool = 'windows'
    user = $user; serial = $serial; changes = @($changes); entries = @($entries)
  }
  Write-KoboBytes "$bk/manifest.json" $Utf8.GetBytes(($manifest | ConvertTo-Json -Depth 5))
  Ok "Backed up $kept file(s) on the Kobo"

  foreach ($w in $writes) {
    try { Write-KoboBytes $w[0] $w[1] } catch { Die "Could not write $($w[0]). Undo is on the library's Kobo setup page." }
    if (-not (Same-Bytes (Read-KoboBytes $w[0]) $w[1])) { Die "After writing $($w[0]) the Kobo reads back something different." }
    switch ($w[0]) {
      $ConfRel { Ok 'Connected the Kobo to your account' }
    }
  }
  foreach ($r in $removes) { Remove-Item -LiteralPath (KPath $r) -Force -ErrorAction SilentlyContinue }
  if ($junk.Count) { Ok "Deleted $($junk.Count) leftover Mac file(s)" }
  if ((Get-ConfValue ($Utf8.GetString([IO.File]::ReadAllBytes((KPath $ConfRel))))) -ne $endpoint) {
    Die "The Kobo's settings do not show the new sync address after writing."
  }

  # ---- 5. done --------------------------------------------------------------------------------
  Say ''
  Write-Host -ForegroundColor Green 'Done.'
  if ((Read-Host 'Eject the Kobo now? (Y/n)') -notmatch '^[nN]') {
    try {
      $drive = $script:Kobo.TrimEnd('\')
      (New-Object -ComObject Shell.Application).Namespace(17).ParseName($drive).InvokeVerb('Eject')
      Start-Sleep -Seconds 2
      Ok 'Ejected. You can unplug the Kobo.'
    } catch { Say 'Windows could not eject it yet. Right-click the Kobo drive in File Explorer and choose Eject.' }
  } else { Say 'Eject it before unplugging: right-click the Kobo drive in File Explorer and choose Eject.' }
  Say ''
  Say 'After unplugging: connect the Kobo to Wi-Fi and tap Sync. Books you put on your Kobo shelf in the library arrive on the Kobo.'
}

try { Main } catch { if ("$_" -ne 'stop') { Write-Host -ForegroundColor Red "Something went wrong: $_" } }

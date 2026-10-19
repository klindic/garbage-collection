#!/usr/bin/env bash
# Wraps hrvatska.html (page source, no <head>) into a standalone page in _site/ and writes its data, calendar feeds
# and Excel files next to it (gen_web.py). The first, Sisak-only feeds and Excel files stay at their old addresses.
set -euo pipefail
cd "$(dirname "$0")"
# OUT: output folder (default _site). SITE_BASE: the address the pages are served from (default: this repo's GitHub
# Pages address). The live site is klindic.github.io/garbage-collection/; any other address (the hrvatska branch is
# published to .../test/ of the same site) gets its own links and feeds, a visible test banner, no visitor counting and
# a noindex tag.
OUT="${OUT:-_site}"
mkdir -p "$OUT"
PROD_BASE="klindic.github.io/garbage-collection/"
REPO="${GITHUB_REPOSITORY:-klindic/garbage-collection}"
OWNER="${REPO%%/*}"
BASE="${SITE_BASE:-${OWNER,,}.github.io/${REPO#*/}/}"
if [ "$BASE" = "$PROD_BASE" ]; then
  ANALYTICS='<script data-goatcounter="https://klindic.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>'
  BANNER=''
  ROBOTS=''
else
  ANALYTICS=''
  BANNER='<div style="position:sticky;top:0;z-index:9;background:#c0392b;color:#fff;font:700 14px/1.4 system-ui,sans-serif;text-align:center;padding:6px 12px">TESTNA VERZIJA, nije za korištenje</div>'
  ROBOTS='<meta name="robots" content="noindex, nofollow">'
fi
export SITE_URL="https://${BASE}"
ICON="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 48'%3E%3Crect x='14' y='1.5' width='12' height='5' rx='1.5' fill='%237b4a2b'/%3E%3Crect x='3' y='6' width='34' height='6' rx='2' fill='%23633b22'/%3E%3Cpath d='M6 12H34L31.5 42H8.5Z' fill='%237b4a2b'/%3E%3Ccircle cx='11.5' cy='43.5' r='3.6' fill='%23141414'/%3E%3Ccircle cx='28.5' cy='43.5' r='3.6' fill='%23141414'/%3E%3C/svg%3E"
{
  cat <<HTML
<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Kalendar odvoza otpada za gradove i općine u Hrvatskoj: upiši ulicu i vidi kad se odvozi koja vrsta otpada, uz podsjetnik u kalendaru.">
<meta name="theme-color" content="#eef2ee" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f1411" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Odvoz">
<link rel="icon" href="${ICON}">
${ROBOTS}${ANALYTICS}
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
HTML
  # <title>, font links and <style> belong in <head>; everything from the first <div> on is body.
  awk '/^<div class="wrap">/{exit} {print}' hrvatska.html
  echo '</head>'
  echo '<body>'
  if [ -n "$BANNER" ]; then echo "$BANNER"; fi
  awk 'f{print} /^<div class="wrap">/{f=1; print}' hrvatska.html
  echo '</body>'
  echo '</html>'
} | sed -e "s|${PROD_BASE}|${BASE}|g" -e "s|klindic.github.io%2Fgarbage-collection%2F|${BASE//\//%2F}|g" > "$OUT/index.html"
python3 gen_web.py "$OUT"
# Addresses from the Sisak-only page (odvoz.html), still in people's calendars and bookmarks.
cp excel/*.xlsx "$OUT/"
python3 gen_ics.py "$OUT"
echo "Built $OUT/index.html"

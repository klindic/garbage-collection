#!/usr/bin/env bash
# Wraps odvoz-zona5.html (artifact source, no <head>) into a standalone page in _site/.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p _site
ICON="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 48'%3E%3Crect x='14' y='1.5' width='12' height='5' rx='1.5' fill='%237b4a2b'/%3E%3Crect x='3' y='6' width='34' height='6' rx='2' fill='%23633b22'/%3E%3Cpath d='M6 12H34L31.5 42H8.5Z' fill='%237b4a2b'/%3E%3Ccircle cx='11.5' cy='43.5' r='3.6' fill='%23141414'/%3E%3Ccircle cx='28.5' cy='43.5' r='3.6' fill='%23141414'/%3E%3C/svg%3E"
{
  cat <<HTML
<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Kalendar odvoza otpada za Zonu 5 u Sisku, 2026.">
<meta name="theme-color" content="#eef2ee" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f1411" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Odvoz">
<link rel="icon" href="${ICON}">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
HTML
  # <title>, font links and <style> belong in <head>; everything from the first <div> on is body.
  awk '/^<div class="wrap">/{exit} {print}' odvoz-zona5.html
  echo '</head>'
  echo '<body>'
  awk 'f{print} /^<div class="wrap">/{f=1; print}' odvoz-zona5.html
  echo '</body>'
  echo '</html>'
} > _site/index.html
cp Raspored_odvoza_Zona5_2026.xlsx _site/
python3 gen_ics.py _site/odvoz.ics
echo "Built _site/index.html"

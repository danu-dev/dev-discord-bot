#!/usr/bin/env python3
"""DEV server AI assistant (Groq) + on-demand QA, owner-only.

Talk to it in #commands. Only the server owner is answered; everyone else is
ignored. Every change the AI proposes is shown first with buttons, and only
runs after the owner presses ✅. Destructive actions (delete/ban/kick) are not
available at all.

    !help        what the bot can do
    !qa          run the full server QA check now
    !qa test     live tests (send/edit/react/delete, role visibility)
    anything else -> AI turns it into a plan you approve
"""

import asyncio
import collections
import datetime
import http.server
import importlib.util
import json
import logging
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

import discord

GUILD_ID = 1572541420298829834
OWNER_ID = 1409498347294822502
COMMANDS_CHANNEL = 1573525454311264356  # #🤖╎bot-commands
QA_CHANNEL = 1573525475437977640        # #📋╎mod-log
MODLOG_CHANNEL = 1573525475437977640    # #📋╎mod-log
BOT_CATEGORY = 1573525392273186849      # 🔒 STAFF
COMMUNITY_CATEGORY = 1573525346261934080  # 💬 CHAT
IDEAS_CHANNEL = 0
STATS_CHANNEL = 1573525454311264356
WELCOME_CHANNEL = 1573522401873104907   # #👋╎welcome
MEMBER_ROLE_ID = 1572712053175754883    # Member
MOD_ROLE_ID = 1572712033500135605       # Moderator
FOUNDER_ROLE_ID = 1572717150089650238   # 👑 ╎ Founder
XP_FILE = os.environ.get("XP_FILE", "dev-xp-data.json")
DISCORD_TOKEN_FILE = "/root/.config/opencode/secrets/discord.token"
GEMINI_KEY_FILE = "/root/.config/opencode/secrets/gemini.key"
GROQ_KEY_FILE = "/root/.config/opencode/secrets/groq.key"
MEMORY_FILE = os.environ.get("MEMORY_FILE", "dev-ai-memory.json")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

BLURPLE, GREEN, RED, GOLD = 0x5865F2, 0x57F287, 0xED4245, 0xFEE75C


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def get_discord_token():
    return os.environ.get("DISCORD_TOKEN") or open(DISCORD_TOKEN_FILE, encoding="utf-8").read().strip()


def _read_secret(env_names, file_path):
    for name in env_names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    if os.path.exists(file_path):
        return read(file_path)
    return ""


def get_gemini_key():
    return _read_secret(("GEMINI_API_KEY", "GEMINI_KEY"), GEMINI_KEY_FILE)


def get_groq_key():
    return _read_secret(("GROQ_KEY", "GROQ_API_KEY"), GROQ_KEY_FILE)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


# --- QA module (reuses local discord_qa.py) -------------------------------
_qa_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "discord_qa.py")
_spec = importlib.util.spec_from_file_location("discord_qa", _qa_path)
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)



# --- client ---------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


# --- helpers --------------------------------------------------------------
def color(hexstr, default=BLURPLE):
    try:
        return int(str(hexstr).lstrip("#"), 16)
    except (TypeError, ValueError):
        return default


def find_channel(guild, name):
    if not name:
        return None
    n = str(name).lstrip("#").strip().lower()
    for c in guild.channels:
        if c.name.lower() == n:
            return c
    for c in guild.channels:  # tolerate missing emoji prefix
        if c.name.lower().endswith(n):
            return c
    return None


NEW_CATEGORIES = {}  # created this run; the gateway cache may lag behind


def find_category(guild, name):
    if not name:
        return None
    n = str(name).strip().lower()
    if n in NEW_CATEGORIES:
        return NEW_CATEGORIES[n]
    for c in guild.categories:
        if c.name.lower() == n or n in c.name.lower():
            return c
    return None


def find_role(guild, name):
    n = str(name).lstrip("@").strip().lower()
    for r in guild.roles:
        if r.name.lower() == n or n in r.name.lower():
            return r
    return None


def find_member(guild, name):
    n = str(name).lstrip("@").strip().lower()
    for m in guild.members:
        if str(m.id) == n or m.name.lower() == n or m.display_name.lower() == n:
            return m
    for m in guild.members:
        if n in m.name.lower() or n in m.display_name.lower():
            return m
    return None




def _has_mod_perm(member, perm_name):
    return member.id == OWNER_ID or getattr(member.guild_permissions, perm_name, False) or member.guild_permissions.administrator


def _parse_target_id(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return int(digits) if len(digits) >= 17 else None

# --- Mod-Log Helpers ------------------------------------------------------
async def _send_modlog(guild, title, description, colour=BLURPLE):
    ch = guild.get_channel(MODLOG_CHANNEL)
    if not ch:
        return
    e = discord.Embed(title=title, description=description[:2000], colour=colour)
    e.set_footer(text=time.strftime("%d %b %Y %H:%M"))
    try:
        await ch.send(embed=e)
    except Exception:
        pass


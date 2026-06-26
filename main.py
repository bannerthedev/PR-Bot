import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ------------ IDs / CONSTANTS ------------
MAIN_GUILD_ID = 1519918685622632448   # main server ID

# ROLES IN MAIN SERVER
MOD_ROLE_ID = 1519918685802725488       # Mod role ID
TRIAL_MOD_ROLE_ID = 1519918685802725487 # Trial Mod role ID

# ---------- Ticket system config ----------
TICKET_PANEL_CHANNEL_ID = 1519918689401442468  # channel where you want the main ticket panel
STAFF_PING_ROLE_ID_MAIN = 1519918685786083397  # role to ping in tickets

# user_id -> dm_ticket_channel_id (in main server)
DM_TICKET_CHANNELS: Dict[int, int] = {}

# MAIN SERVER LOG CHANNEL (for ban/unban/false-ban/kick logs)
LOG_CHANNEL_ID = 1519918686843179196

# Channel for deleted-message logs (in main server)
DELETE_LOG_CHANNEL_ID = 1519918686843179197

MAIN_SERVER_INVITE = "https://discord.gg/ynaZV6epty"
SERVER_NAME = "Paper Rex"

# Words/phrases to auto-delete (case-insensitive)
BAD_WORDS = [
    "fuck", "bitch", "asshole", "bullshit", "bastard", "cock", "dammit", "dick",
    "dick head", "dickhead", "dumb ass", "dumbass", "fucker", "fucking", "goddamnit",
    "jack ass", "jackass", "motherfucker", "nigga", "pussy", "sisterfuck",
    "niggers","penis", "cocksucker", "retartd", "retarted", "rtrd", "nga", 
    "stfu","b1tch", "a$$", "jew",
]

# ---- Automod master switch (runtime toggle) ----
AUTOMOD_ENABLED = False  # False = OFF, True = ON

# Only these users can run /automod
AUTOMOD_ALLOWED_USER_IDS = {
    1101643714033623120,
    1317315295400165446,
    1252981295454224390,
}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.dm_messages = True
intents.message_content = True  # enable in Developer Portal as well

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Shared helpers ----------

async def generate_ticket_ai_reply(message: discord.Message) -> str:
    if not OPENAI_API_KEY:
        return "Paper Rex Assistant here. AI not configured."

    user_text = message.content or ""
    channel_name = getattr(message.channel, "name", "unknown-channel")

    system_prompt = (
        "You are Paper Rex Assistant, a helpful support agent inside a Discord ticket system. "
        "Answer clearly, politely, and concisely. If the question is about server rules, reference "
        "these docs when relevant:\n"
        f"{SERVER_RULES_LINK}\n{GAME_RULEBOOK_LINK}\n\n"
        "Do not pretend to be staff; you are an assistant."
    )

    models = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]

    for model in models:
        try:
            resp = await openai_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"(Channel: {channel_name})\n\n{user_text}"},
                ],
                temperature=0.4,
                max_tokens=300,
            )
            if resp and getattr(resp, "choices", None):
                return resp.choices[0].message.content.strip()
        except Exception as e:
            try:
                print(f"Model {model} failed: {e}")
            except Exception:
                pass
            continue

    return "Paper Rex Assistant: I had trouble generating a response right now. Please try again or ask staff."

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

EST = ZoneInfo("America/New_York")

def format_time(dt: datetime) -> str:
    dt = dt.astimezone(EST)
    return dt.strftime("%m/%d/%Y %I:%M %p EST")

def parse_duration(text: str) -> Optional[timedelta]:
    if not text:
        return None
    text = text.strip().lower()
    if text in ("perm", "permanent", "perma", "permban", "perm ban", "permanent ban"):
        return None
    num = ""
    unit = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch.isalpha():
            unit += ch
        else:
            continue
    if not num:
        return None
    n = int(num)
    if unit in ("d", "day", "days"):
        return timedelta(days=n)
    if unit in ("h", "hr", "hour", "hours"):
        return timedelta(hours=n)
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return timedelta(minutes=n)
    if unit in ("s", "sec", "secs", "second", "seconds"):
        return timedelta(seconds=n)
    if unit in ("mo", "month", "months"):
        return timedelta(days=30 * n)
    return None

def get_delete_log_channel() -> Optional[discord.TextChannel]:
    ch = bot.get_channel(DELETE_LOG_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None

def format_remaining(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Expired"
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)

async def setup_countdown(message: discord.Message, end_time: datetime):
    while True:
        now = now_utc()
        remaining = end_time - now
        if remaining.total_seconds() <= 0:
            try:
                embed = message.embeds[0]
                for i, field in enumerate(embed.fields):
                    if field.name.lower().startswith("duration"):
                        embed.set_field_at(i, name=embed.fields[i].name, value="Expired", inline=False)
                        break
                await message.edit(embed=embed)
            except Exception:
                pass
            break
        remaining_text = format_remaining(remaining)
        try:
            embed = message.embeds[0]
            for i, field in enumerate(embed.fields):
                if field.name.lower().startswith("duration"):
                    embed.set_field_at(i, name=embed.fields[i].name, value=remaining_text, inline=False)
                    break
            await message.edit(embed=embed)
        except Exception:
            break
        await asyncio.sleep(60)

# ---------- Suspicious detection helpers ----------

SUSPICIOUS_KEYWORDS = {
    "withdraw", "withdrawal", "promo", "promo code", "activate", "activation",
    "bonus", "rakeback", "deposit", "launch", "click here", "claim", "earn",
    "giveaway", "free", "crypto", "usdt", "btc", "ethereum", "metamask"
}
SUSPICIOUS_DOMAINS = {"tiny.cc", "bit.ly", "free-giveaway.example"}
delete_images_always = False

def message_contains_suspicious_text(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in low:
            return True
    return False

def embeds_contain_suspicious(embed: discord.Embed) -> bool:
    if embed.title and message_contains_suspicious_text(embed.title):
        return True
    if embed.description and message_contains_suspicious_text(embed.description):
        return True
    if embed.author and getattr(embed.author, "name", None) and message_contains_suspicious_text(embed.author.name):
        return True
    for f in embed.fields:
        if message_contains_suspicious_text(f.name) or message_contains_suspicious_text(f.value):
            return True
    return False

def attachments_or_embeds_have_images(message: discord.Message) -> bool:
    if message.attachments:
        for att in message.attachments:
            if any(att.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return True
    for e in message.embeds:
        if e.image or e.thumbnail:
            return True
    return False

def message_has_suspicious_link(message: discord.Message) -> bool:
    text = (message.content or "") + " "
    parts = text.split()
    for p in parts:
        if p.startswith("http://") or p.startswith("https://") or "." in p:
            for d in SUSPICIOUS_DOMAINS:
                if d in p:
                    return True
    for e in message.embeds:
        if getattr(e, "url", None):
            for d in SUSPICIOUS_DOMAINS:
                if d in e.url:
                    return True
    return False

# ---------- Permission helpers ----------

def is_mod_or_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if MOD_ROLE_ID:
        role = member.guild.get_role(MOD_ROLE_ID)
        if role and role in member.roles:
            return True
    return False

def can_timeout(member: discord.Member) -> bool:
    if is_mod_or_admin(member):
        return True
    if TRIAL_MOD_ROLE_ID:
        trial_role = member.guild.get_role(TRIAL_MOD_ROLE_ID)
        if trial_role and trial_role in member.roles:
            return True
    return False

def get_log_channel() -> Optional[discord.TextChannel]:
    ch = bot.get_channel(LOG_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None

# ---------- Global state ----------
permanent_bans: set[int] = set()
temp_bans: Dict[int, datetime] = {}

case_counter = 1
def get_next_case_id() -> int:
    global case_counter
    cid = case_counter
    case_counter += 1
    return cid

# Auto-mod escalation state
bad_word_offenses: Dict[int, int] = {}
last_offense_time: Dict[int, datetime] = {}
MAX_TIMEOUT_DAYS = 30
BAN_ON_REOFFEND_WITHIN_DAYS = 7
BAN_DURATION_DAYS = 60

# ============================================================
#                       /submit-report
# ============================================================

class SRDurationReasonModal(discord.ui.Modal, title="Action (duration + reason)"):
    duration = discord.ui.TextInput(
        label="Duration (e.g. 7d, 12h, 30m, perm)",
        required=True,
        max_length=20,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, action: str, target: discord.User):
        super().__init__()
        self.action = action  # "ban" or "mute"
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "This command can only be used in the main server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
            await interaction.response.send_message(
                "You must be a moderator or admin to use this command.",
                ephemeral=True,
            )
            return

        raw_duration = self.duration.value.strip()
        td = parse_duration(raw_duration)  # None => permanent
        reason = self.reason.value.strip() or "No reason provided."

        # ---------------- BAN ----------------
        if self.action == "ban":
            guild = interaction.guild
            try:
                await guild.ban(
                    discord.Object(id=self.target.id),
                    reason=reason,
                    delete_message_seconds=0,
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to ban user: `{e}`",
                    ephemeral=True,
                )
                return

            # DM user (best effort)
            try:
                dm_embed = discord.Embed(
                    title=f"You have been banned from {SERVER_NAME}",
                    description=f"**Reason:** {reason}",
                    color=discord.Color.dark_red(),
                )
                await self.target.send(embed=dm_embed)
            except Exception:
                pass

            # Log to mod log channel
            log_ch = get_log_channel()
            if log_ch is not None:
                case_id = get_next_case_id()
                now = now_utc()
                offender_str = f"{self.target.id} {getattr(self.target, 'mention', '')}"
                dur_text = "Permanent" if td is None else raw_duration
                embed = discord.Embed(
                    title=f"ban | case {case_id}",
                    color=discord.Color.dark_red(),
                )
                embed.add_field(name="Offender:", value=offender_str, inline=False)
                embed.add_field(name="Reason:", value=reason, inline=False)
                embed.add_field(name="Duration:", value=dur_text, inline=False)
                embed.add_field(
                    name="ID / Time:",
                    value=f"{self.target.id} • {format_time(now)}",
                    inline=False,
                )
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            await interaction.response.send_message(
                f"✅ Banned {self.target.mention} (`{self.target.id}`)\n**Reason:** {reason}",
                ephemeral=True,
            )
            return

        # ---------------- MUTE / TIMEOUT ----------------
        elif self.action == "mute":
            if td is None:
                await interaction.response.send_message(
                    "Mute must have a finite duration (no permanent mutes via this form).",
                    ephemeral=True,
                )
                return

            guild = interaction.guild
            member = guild.get_member(self.target.id)
            if member is None:
                await interaction.response.send_message(
                    "That user is not in the server.",
                    ephemeral=True,
                )
                return

            end_time = now_utc() + td
            try:
                await member.edit(timeout=end_time, reason=reason)
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to timeout user: `{e}`",
                    ephemeral=True,
                )
                return

            # DM user (best effort)
            try:
                dm_embed = discord.Embed(
                    title=f"You have been muted in {SERVER_NAME}",
                    description=(
                        f"**Duration:** {raw_duration}\n"
                        f"**Reason:** {reason}"
                    ),
                    color=discord.Color.orange(),
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass

            # Log to mod log channel
            log_ch = get_log_channel()
            if log_ch is not None:
                case_id = get_next_case_id()
                now = now_utc()
                offender_str = f"{member.id} {member.mention}"
                embed = discord.Embed(
                    title=f"mute | case {case_id}",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Offender:", value=offender_str, inline=False)
                embed.add_field(name="Reason:", value=reason, inline=False)
                embed.add_field(name="Duration:", value=raw_duration, inline=False)
                embed.add_field(
                    name="ID / Time:",
                    value=f"{member.id} • {format_time(now)}",
                    inline=False,
                )
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            await interaction.response.send_message(
                f"✅ Muted {member.mention} (`{member.id}`) for `{raw_duration}`\n**Reason:** {reason}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Unknown action.",
            ephemeral=True,
        )

class SRReasonOnlyModal(discord.ui.Modal, title="Action (reason only)"):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, action: str, target: discord.User):
        super().__init__()
        self.action = action  # "warning"
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "This command can only be used in the main server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
            await interaction.response.send_message(
                "You must be a moderator or admin to use this command.",
                ephemeral=True,
            )
            return

        reason = self.reason.value.strip() or "No reason provided."

        if self.action != "warning":
            await interaction.response.send_message(
                "Unknown action.",
                ephemeral=True,
            )
            return

        # DM user warning (best effort)
        try:
            dm_embed = discord.Embed(
                title=f"You have received a warning in {SERVER_NAME}",
                description=f"**Reason:** {reason}",
                color=discord.Color.yellow(),
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log warning
        log_ch = get_log_channel()
        if log_ch is not None:
            case_id = get_next_case_id()
            now = now_utc()
            offender_str = f"{self.target.id} {getattr(self.target, 'mention', '')}"
            embed = discord.Embed(
                title=f"warning | case {case_id}",
                color=discord.Color.yellow(),
            )
            embed.add_field(name="Offender:", value=offender_str, inline=False)
            embed.add_field(name="Reason:", value=reason, inline=False)
            embed.add_field(
                name="ID / Time:",
                value=f"{self.target.id} • {format_time(now)}",
                inline=False,
            )
            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message(
            f"✅ Warning recorded for {self.target.mention} (`{self.target.id}`)\n**Reason:** {reason}",
            ephemeral=True,
        )


class SRMemberSelect(discord.ui.UserSelect):
    def __init__(self, action: str):
        super().__init__(
            placeholder="Select a member...",
            min_values=1,
            max_values=1,
            custom_id="sr_member_select",
        )
        self.action = action  # "ban" / "mute" / "warning"

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        action = self.action

        if action in ("ban", "mute"):
            modal = SRDurationReasonModal(action=action, target=member)
        else:
            modal = SRReasonOnlyModal(action=action, target=member)

        await interaction.response.send_modal(modal)


class SRMemberSelectView(discord.ui.View):
    def __init__(self, action: str, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRMemberSelect(action))


class SRActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Warning", value="warning"),
            discord.SelectOption(label="Mute/Timeout", value="mute"),
        ]
        super().__init__(
            placeholder="Choose an action...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="sr_action_select",
        )

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        view = SRMemberSelectView(action)
        await interaction.response.edit_message(
            content=f"Action selected: **{action.capitalize()}**. Now choose a member:",
            view=view,
        )


class SRActionSelectView(discord.ui.View):
    def __init__(self, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRActionSelect())


@bot.tree.command(name="submit-report", description="Submit a moderation report (mods+ only)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def submit_report(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(
            "This command can only be used in the main server.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message(
            "You must be a moderator or admin to use this command.",
            ephemeral=True,
        )
        return

    view = SRActionSelectView()
    await interaction.response.send_message(
        "Choose an action for this report:",
        view=view,
        ephemeral=True,
    )


@bot.tree.command(
    name="manage-ticket",
    description="Admin: open/close a ticket type on the ticket panel.",
)
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
@app_commands.describe(
    action="Open or Close the ticket type",
    ticket_type="Which ticket type to manage",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Open", value="open"),
        app_commands.Choice(name="Close", value="close"),
    ],
    ticket_type=[
        app_commands.Choice(name="Website Help", value="Website Help"),
        app_commands.Choice(name="General Help", value="General Help"),
        app_commands.Choice(name="Report A Player", value="Report A Player"),
    ],
)
async def manage_ticket(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    ticket_type: app_commands.Choice[str],
):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("Use this in the main server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    panel_ch = bot.get_channel(TICKET_PANEL_CHANNEL_ID) or interaction.channel
    if not isinstance(panel_ch, discord.TextChannel):
        await interaction.response.send_message("Ticket panel channel not found.", ephemeral=True)
        return

    target_label = ticket_type.value
    should_disable = (action.value == "close")
    updated_any = False

    try:
        async for msg in panel_ch.history(limit=200):
            if msg.author.id != bot.user.id:
                continue
            emb = msg.embeds[0] if msg.embeds else None
            if not emb or (emb.title or "").lower() != "ticket system":
                continue

            try:
                view = discord.ui.View.from_message(msg)
            except Exception:
                continue

            changed = False
            for child in view.children:
                if isinstance(child, discord.ui.Button) and (child.label or "") == target_label:
                    child.disabled = should_disable
                    changed = True

            if not changed:
                continue

            try:
                await msg.edit(view=view)
                updated_any = True
            except Exception:
                continue
    except Exception:
        pass

    if updated_any:
        await interaction.response.send_message(
            f"{'Closed' if should_disable else 'Opened'} ticket type `{target_label}` on the ticket panel.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "Could not find an existing ticket panel message to update.",
            ephemeral=True,
        )


# ============================================================
#                       on_message (combined handler)
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    # Ensure commands still processed
    await bot.process_commands(message)

    if message.author.bot:
        return

    # ---------- TICKET AI + STAFF REQUEST ----------
    if message.guild is not None and message.guild.id == MAIN_GUILD_ID:
        ch = message.channel
        ch_name = (ch.name or "").lower() if isinstance(ch, discord.TextChannel) else ""

        is_ticket_channel = ch_name.startswith("ticket-") or ch_name.startswith("dm-")

        if is_ticket_channel:
            content_low = (message.content or "").lower()

            # 1) user asks to speak to staff
            if "i would like to speak to the staff" in content_low:
                staff_role = message.guild.get_role(STAFF_PING_ROLE_ID_MAIN) or message.guild.get_role(MOD_ROLE_ID)
                staff_ping = staff_role.mention if staff_role else "@staff"
                try:
                    await ch.send(f"{staff_ping} {message.author.mention} wants to talk to you.")
                except Exception:
                    pass

            # 2) user mentions the bot -> AI reply
            if bot.user and bot.user in message.mentions:
                ai_reply = await generate_ticket_ai_reply(message)
                try:
                    await ch.send(ai_reply)
                except Exception:
                    pass

    # -------- PING-TO-DELETE FEATURE ----------
    if (
        message.guild is not None
        and message.guild.id == MAIN_GUILD_ID
        and bot.user is not None
        and bot.user in message.mentions
        and message.reference is not None
        and isinstance(message.reference.resolved, discord.Message)
    ):
        target_msg: discord.Message = message.reference.resolved
        try:
            await target_msg.delete()
        except Exception:
            pass

    # -------- AUTO-MOD BAD_WORDS (escalating timeouts -> ban) ----------
    if AUTOMOD_ENABLED and message.guild is not None and message.guild.id == MAIN_GUILD_ID and isinstance(message.channel, discord.TextChannel):
        content_lower = (message.content or "").lower()
        if any(bad in content_lower for bad in BAD_WORDS):
            uid = message.author.id
            now = now_utc()

            # reoffend within X days -> schedule ban
            last_time = last_offense_time.get(uid)
            if last_time is not None and (now - last_time) <= timedelta(days=BAN_ON_REOFFEND_WITHIN_DAYS):
                end_time = now + timedelta(days=BAN_DURATION_DAYS)
                if isinstance(message.author, discord.Member):
                    try:
                        await message.author.edit(timeout=end_time)
                    except Exception:
                        pass

                temp_bans[uid] = end_time

                try:
                    await message.author.send(
                        f"You have been timed out in {SERVER_NAME} and will be banned after the timeout expires due to repeated rule violations."
                    )
                except Exception:
                    pass

                log_ch = get_log_channel()
                if log_ch is not None:
                    embed = discord.Embed(title="Auto-schedule ban (case)", color=discord.Color.dark_red())
                    embed.add_field(name="Offender:", value=f"{uid} {getattr(message.author, 'mention', '')}", inline=False)
                    embed.add_field(
                        name="Reason:",
                        value=f"Repeated bad-language offenses. Timed out and scheduled ban for {BAN_DURATION_DAYS} days.",
                        inline=False
                    )
                    embed.add_field(name="Message", value=(message.content or "[no text]")[:1024], inline=False)
                    embed.set_footer(text=format_time(now))
                    try:
                        await log_ch.send(embed=embed)
                    except Exception:
                        pass

                bad_word_offenses.pop(uid, None)
                last_offense_time.pop(uid, None)

                try:
                    await message.delete()
                except Exception:
                    pass

                return

            # first / escalating offense
            count = bad_word_offenses.get(uid, 0) + 1
            bad_word_offenses[uid] = count
            last_offense_time[uid] = now

            days = min(count, MAX_TIMEOUT_DAYS)
            end_time = now + timedelta(days=days)

            if isinstance(message.author, discord.Member):
                try:
                    await message.author.edit(timeout=end_time)
                except Exception:
                    pass

            # DM the user (rules removed)
            try:
                embed = discord.Embed(
                    title=f"You Have Been Muted In {SERVER_NAME}",
                    description=(
                        f"You used disallowed language. This is offense #{count}.\n"
                        f"You have been timed out for {days} day{'s' if days != 1 else ''}."
                    ),
                    color=discord.Color.orange()
                )
                embed.set_footer(text=format_time(now))
                await message.author.send(embed=embed)
            except Exception:
                pass

            try:
                await message.delete()
            except Exception:
                pass

            log_ch = get_delete_log_channel()
            if log_ch is not None:
                channel_name = f"#{message.channel.name}"
                embed = discord.Embed(
                    title="Auto-timeout (bad word)",
                    description=f"Deleted and timed out in {channel_name}",
                    color=discord.Color.dark_orange()
                )
                embed.add_field(name="Author", value=f"{message.author} ({uid})", inline=False)
                embed.add_field(name="Offense #", value=str(count), inline=True)
                embed.add_field(name="Duration", value=f"{days} day{'s' if days != 1 else ''}", inline=True)
                embed.add_field(name="Message", value=(message.content or "[no text]")[:1024], inline=False)
                embed.set_footer(text=format_time(now))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            return

    # -------- SUSPICIOUS PROMO/SCAM DETECTION ----------
    if AUTOMOD_ENABLED:
        try:
            suspicious = False

            if message_contains_suspicious_text(message.content):
                suspicious = True

            if not suspicious:
                for e in message.embeds:
                    if embeds_contain_suspicious(e):
                        suspicious = True
                        break

            has_image = attachments_or_embeds_have_images(message)
            if not suspicious and has_image:
                if delete_images_always:
                    suspicious = True
                else:
                    if message_contains_suspicious_text(message.content):
                        suspicious = True
                    else:
                        for e in message.embeds:
                            if embeds_contain_suspicious(e):
                                suspicious = True
                                break
                        if not suspicious and message_has_suspicious_link(message):
                            suspicious = True

            if not suspicious and message_has_suspicious_link(message):
                suspicious = True

            if suspicious:
                try:
                    await message.delete()
                except Exception:
                    pass

                log_ch = get_delete_log_channel()
                if log_ch is not None:
                    channel_name = f"#{message.channel.name}" if isinstance(message.channel, discord.TextChannel) else "DM"
                    embed = discord.Embed(
                        title="Auto-deleted suspicious message",
                        description=f"Deleted in {channel_name}",
                        color=discord.Color.dark_red()
                    )
                    embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
                    embed.add_field(name="Content", value=(message.content or "[no text]")[:1024], inline=False)
                    embed.add_field(name="Message ID", value=str(message.id), inline=False)
                    if message.attachments:
                        urls = "\n".join(att.url for att in message.attachments)
                        embed.add_field(name="Attachments", value=urls[:1024], inline=False)
                    embed.set_footer(text=format_time(now_utc()))
                    try:
                        await log_ch.send(embed=embed)
                    except Exception:
                        pass

                return
        except Exception:
            pass


    # -------- SUSPICIOUS PROMO/SCAM DETECTION ----------
    if AUTOMOD_ENABLED:
        try:
            suspicious = False

            if message_contains_suspicious_text(message.content):
                suspicious = True

            if not suspicious:
                for e in message.embeds:
                    if embeds_contain_suspicious(e):
                        suspicious = True
                        break

            has_image = attachments_or_embeds_have_images(message)
            if not suspicious and has_image:
                if delete_images_always:
                    suspicious = True
                else:
                    if message_contains_suspicious_text(message.content):
                        suspicious = True
                    else:
                        for e in message.embeds:
                            if embeds_contain_suspicious(e):
                                suspicious = True
                                break
                        if not suspicious and message_has_suspicious_link(message):
                            suspicious = True

            if not suspicious and message_has_suspicious_link(message):
                suspicious = True

            if suspicious:
                try:
                    await message.delete()
                except Exception:
                    pass

                log_ch = get_delete_log_channel()
                if log_ch is not None:
                    channel_name = f"#{message.channel.name}" if isinstance(message.channel, discord.TextChannel) else "DM"
                    embed = discord.Embed(
                        title="Auto-deleted suspicious message",
                        description=f"Deleted in {channel_name}",
                        color=discord.Color.dark_red()
                    )
                    embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
                    embed.add_field(name="Content", value=(message.content or "[no text]")[:1024], inline=False)
                    embed.add_field(name="Message ID", value=str(message.id), inline=False)
                    if message.attachments:
                        urls = "\n".join(att.url for att in message.attachments)
                        embed.add_field(name="Attachments", value=urls[:1024], inline=False)
                    embed.set_footer(text=format_time(now_utc()))
                    try:
                        await log_ch.send(embed=embed)
                    except Exception:
                        pass

                return
        except Exception:
            pass


# ============================================================
#                      Ticket System
# ============================================================

class TicketAskAIView(discord.ui.View):
    """Simple Ask AI helper for DM or in-server, shows instructions."""
    def __init__(self, for_user: discord.User, is_dm_ticket: bool):
        super().__init__(timeout=None)
        self.for_user = for_user
        self.is_dm_ticket = is_dm_ticket

    @discord.ui.button(label="Ask AI", style=discord.ButtonStyle.primary)
    async def ask_ai(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.for_user.id and not is_mod_or_admin(interaction.user):
            await interaction.response.send_message("This Ask AI button is not for you.", ephemeral=True)
            return

        msg = (
            "You can ask Paper Rex Assistant questions by mentioning the bot in this ticket.\n\n"
            "If you need a real staff member, say exactly:\n"
            "`I would like to speak to the staff`\n\n"
            "When you say that, the bot will ping staff for you."
        )
        await interaction.response.send_message(msg, ephemeral=True)


class TicketAddMemberSelect(discord.ui.UserSelect):
    def __init__(self, channel: discord.TextChannel):
        super().__init__(
            placeholder="Select a member to add...",
            min_values=1,
            max_values=1,
        )
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        if not is_mod_or_admin(interaction.user):
            await interaction.response.send_message("Only staff can add members.", ephemeral=True)
            return

        member = self.values[0]
        overwrites = self.channel.overwrites or {}
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            await self.channel.edit(overwrites=overwrites)
        except Exception as e:
            await interaction.response.send_message(f"Failed to add member: `{e}`", ephemeral=True)
            return

        await interaction.response.send_message(f"{member.mention} has been added to this ticket.", ephemeral=True)


class TicketChannelView(discord.ui.View):
    """View for in-server ticket channels (user + staff)."""
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id or is_mod_or_admin(interaction.user):
            return True
        await interaction.response.send_message("You are not allowed to use these buttons.", ephemeral=True)
        return False

    @discord.ui.button(label="Add Member", style=discord.ButtonStyle.secondary)
    async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Not a text channel.", ephemeral=True)
            return
        view = discord.ui.View(timeout=60)
        view.add_item(TicketAddMemberSelect(ch))
        await interaction.response.send_message("Select a member to add:", view=view, ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Not a text channel.", ephemeral=True)
            return

        staff_role = interaction.guild.get_role(STAFF_PING_ROLE_ID_MAIN) if interaction.guild else None
        staff_ping = staff_role.mention if staff_role else "@staff"
        try:
            await ch.send(f"{staff_ping}\nDo you want to close this ticket?")
        except Exception:
            pass

        embed = discord.Embed(
            title="Close Ticket?",
            description="Do you want to close this ticket?",
            color=discord.Color.blue(),
        )
        view = TicketCloseConfirmView(channel_id=ch.id, is_dm=False, owner_id=self.owner_id)
        try:
            await ch.send(embed=embed, view=view)
        except Exception:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message("Requested ticket close.", ephemeral=True)

    @discord.ui.button(label="Ask AI", style=discord.ButtonStyle.primary)
    async def ask_ai(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.guild.get_member(self.owner_id) if interaction.guild else interaction.user
        view = TicketAskAIView(for_user=user, is_dm_ticket=False)
        await interaction.response.send_message(
            "Ask AI opened. Read the instructions below.",
            view=view,
            ephemeral=True,
        )


class DMTicketChannelView(discord.ui.View):
    """Staff-side view for DM tickets (inside DM ticket channel in server)."""
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_mod_or_admin(interaction.user):
            return True
        await interaction.response.send_message("Only staff can use these buttons.", ephemeral=True)
        return False

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Not a text channel.", ephemeral=True)
            return

        staff_role = interaction.guild.get_role(STAFF_PING_ROLE_ID_MAIN) if interaction.guild else None
        staff_ping = staff_role.mention if staff_role else "@staff"
        try:
            await ch.send(f"{staff_ping}\nDo you want to close this DM ticket?")
        except Exception:
            pass

        embed = discord.Embed(
            title="Close Ticket?",
            description="Staff: do you want to close this DM ticket?",
            color=discord.Color.blue(),
        )
        view = TicketCloseConfirmView(channel_id=ch.id, is_dm=True, owner_id=self.owner_id)
        try:
            await ch.send(embed=embed, view=view)
        except Exception:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message("Requested ticket close.", ephemeral=True)

    @discord.ui.button(label="Ask AI", style=discord.ButtonStyle.primary)
    async def ask_ai(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.client.get_user(self.owner_id) or interaction.user
        view = TicketAskAIView(for_user=user, is_dm_ticket=True)
        await interaction.response.send_message(
            "Ask AI instructions sent.",
            view=view,
            ephemeral=True,
        )


class UserDMTicketView(discord.ui.View):
    """User-side buttons in DM: Close Ticket + Ask AI."""
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This ticket is not for you.", ephemeral=True)
        return False

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch_id = DM_TICKET_CHANNELS.get(self.owner_id)
        ch = interaction.client.get_channel(ch_id) if ch_id else None
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Could not find your ticket channel in the server.", ephemeral=True)
            return

        guild = ch.guild
        staff_role = guild.get_role(STAFF_PING_ROLE_ID_MAIN) if guild else None
        staff_ping = staff_role.mention if staff_role else "@staff"

        try:
            await ch.send(f"{staff_ping}\nDo you want to close this ticket?")
        except Exception:
            pass

        embed = discord.Embed(
            title="Close Ticket?",
            description="Staff: do you want to close this DM ticket?",
            color=discord.Color.blue(),
        )
        view = TicketCloseConfirmView(channel_id=ch.id, is_dm=True, owner_id=self.owner_id)
        try:
            await ch.send(embed=embed, view=view)
        except Exception:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message("Requested ticket close. Staff will review it.", ephemeral=True)

    @discord.ui.button(label="Ask AI", style=discord.ButtonStyle.primary)
    async def ask_ai(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        view = TicketAskAIView(for_user=user, is_dm_ticket=True)
        await interaction.response.send_message(
            "Ask AI instructions sent.",
            view=view,
            ephemeral=True,
        )


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, channel_id: int, is_dm: bool = False, owner_id: Optional[int] = None):
        super().__init__(timeout=120)
        self.channel_id = channel_id
        self.is_dm = is_dm
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_mod_or_admin(interaction.user):
            await interaction.response.send_message("Only staff can confirm closing.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.client.get_channel(self.channel_id)
        closer = interaction.user

        # DM the ticket owner if we know them
        if self.owner_id:
            try:
                owner_user = interaction.client.get_user(self.owner_id) or await interaction.client.fetch_user(self.owner_id)
            except Exception:
                owner_user = None

            if owner_user:
                try:
                    await owner_user.send(f"{owner_user.mention} Your ticket has been closed by {closer.mention}")
                except Exception:
                    pass

        # For DM tickets, also announce in the staff channel before deleting
        if self.is_dm and isinstance(ch, discord.TextChannel) and self.owner_id:
            user = interaction.client.get_user(self.owner_id)
            if user:
                try:
                    await ch.send(f"{user.mention} has closed your ticket.")
                except Exception:
                    pass

        if isinstance(ch, discord.TextChannel):
            if self.is_dm and self.owner_id in DM_TICKET_CHANNELS:
                DM_TICKET_CHANNELS.pop(self.owner_id, None)
            try:
                await ch.delete(reason=f"Ticket closed by {closer}")
            except Exception:
                pass

        await interaction.response.send_message("Ticket closed.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.secondary)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Ticket close cancelled.", ephemeral=True)
        self.stop()


class TicketLocationView(discord.ui.View):
    """Second step: In Server / DM."""
    def __init__(self, dm_category_id: int, server_category_id: int, ticket_type: str):
        super().__init__(timeout=120)
        self.dm_category_id = dm_category_id
        self.server_category_id = server_category_id
        self.ticket_type = ticket_type

    async def _create_server_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return

        cat = guild.get_channel(self.server_category_id)
        if not isinstance(cat, discord.CategoryChannel):
            await interaction.response.send_message("In-server ticket category not found.", ephemeral=True)
            return

        user = interaction.user
        name_base = f"ticket-{user.name}".lower().replace(" ", "-")
        channel_name = name_base[:90]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        for rid in (MOD_ROLE_ID, TRIAL_MOD_ROLE_ID):
            if not rid:
                continue
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ch = await guild.create_text_channel(
            name=channel_name,
            category=cat,
            overwrites=overwrites,
            reason=f"{self.ticket_type} ticket for {user}",
        )

        embed = discord.Embed(
            title=f"{self.ticket_type} Ticket",
            description="Please give the staff your question and all relevant information.",
            color=discord.Color.blue(),
        )
        await ch.send(embed=embed, view=TicketChannelView(owner_id=user.id))
        await interaction.response.send_message(f"Your ticket has been created: {ch.mention}", ephemeral=True)

    async def _create_dm_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return

        cat = guild.get_channel(self.dm_category_id)
        if not isinstance(cat, discord.CategoryChannel):
            await interaction.response.send_message("DM ticket category not found.", ephemeral=True)
            return

        user = interaction.user
        name_base = f"dm-{user.name}".lower().replace(" ", "-")
        channel_name = name_base[:90]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        for rid in (MOD_ROLE_ID, TRIAL_MOD_ROLE_ID):
            if not rid:
                continue
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ch = await guild.create_text_channel(
            name=channel_name,
            category=cat,
            overwrites=overwrites,
            reason=f"DM {self.ticket_type} ticket for {user}",
        )

        DM_TICKET_CHANNELS[user.id] = ch.id

        # staff ping above embed in staff channel
        staff_role = guild.get_role(STAFF_PING_ROLE_ID_MAIN)
        staff_ping = staff_role.mention if staff_role else "@staff"
        try:
            await ch.send(staff_ping)
        except Exception:
            pass

        embed_staff = discord.Embed(
            title=f"{self.ticket_type} DM Ticket",
            description="This channel relays messages between staff and the user via DMs.",
            color=discord.Color.blue(),
        )
        try:
            await ch.send(embed=embed_staff, view=DMTicketChannelView(owner_id=user.id))
        except Exception:
            try:
                await ch.send(embed=embed_staff)
            except Exception:
                pass

        # user DM: ping + embed + buttons
        try:
            await user.send(user.mention)
            dm_embed = discord.Embed(
                title=f"{self.ticket_type} Ticket",
                description=(
                    "This ticket is for you and the staff to talk and help you out.\n"
                    "You can send messages here and they will be seen by staff."
                ),
                color=discord.Color.blue(),
            )
            await user.send(embed=dm_embed, view=UserDMTicketView(owner_id=user.id))
        except Exception:
            pass

        await interaction.response.send_message("Your DM ticket has been created. Check your DMs.", ephemeral=True)

    @discord.ui.button(label="In Server", style=discord.ButtonStyle.success)
    async def in_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create_server_ticket(interaction)

    @discord.ui.button(label="DM", style=discord.ButtonStyle.secondary)
    async def dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create_dm_ticket(interaction)


class TicketTypeView(discord.ui.View):
    """Main panel: Website Help / General Help / Report A Player."""
    def __init__(self, dm_category_id: int, server_category_id: int):
        super().__init__(timeout=None)
        self.dm_category_id = dm_category_id
        self.server_category_id = server_category_id

    async def _send_location_choice(self, interaction: discord.Interaction, ticket_type: str):
        embed = discord.Embed(
            title=f"{ticket_type} Ticket",
            description="What would you like:",
            color=discord.Color.blue(),
        )
        view = TicketLocationView(
            dm_category_id=self.dm_category_id,
            server_category_id=self.server_category_id,
            ticket_type=ticket_type,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="General Help", style=discord.ButtonStyle.primary)
    async def general_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_location_choice(interaction, "General Help")

    @discord.ui.button(label="Report A Player", style=discord.ButtonStyle.danger)
    async def report_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_location_choice(interaction, "Report A Player")


@bot.tree.command(
    name="create-ticket",
    description="Create the main ticket message (admins only).",
)
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
@app_commands.describe(
    dm_category="Category where DM tickets are created (server side)",
    in_server_category="Category where in-server tickets are created",
)
async def create_ticket(
    interaction: discord.Interaction,
    dm_category: discord.CategoryChannel,
    in_server_category: discord.CategoryChannel,
):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    # choose target channel
    target_ch = bot.get_channel(TICKET_PANEL_CHANNEL_ID)
    if not isinstance(target_ch, discord.TextChannel):
        target_ch = interaction.channel

    # main info embed (blue)
    desc = (
        "Community Support and Report Ticket Bot.\n"
        "❌ Misuse of Tickets will result in Punishment ❌\n"
        "- 1st Offense - Warning\n"
        "- 2nd Offense - Timeout (1 Day - 1 Week)\n"
        "- 3rd Offense - Timeout Again (Double The Previous)\n"
        "- 4th Offense - Stacking 1 Month Bans\n\n"
        "Open the ticket type that fits your issue best.\n\n"
        "**General Help** – Get help with general questions or issues.\n"
        "**Report A Player** – Report a player for breaking the rules. You can send links, videos, and photos as evidence.\n"
    )

    embed = discord.Embed(
        title="Ticket System",
        description=desc,
        color=discord.Color.blue(),
    )

    view = TicketTypeView(dm_category_id=dm_category.id, server_category_id=in_server_category.id)

    try:
        await target_ch.send(embed=embed, view=view)
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to send ticket panel: `{e}`",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"Ticket panel created in {target_ch.mention}.",
        ephemeral=True,
    )


# ---------------- PURGE GROUP ----------------

purge = app_commands.Group(
    name="purge",
    description="Purge messages in a channel"
)

@purge.command(name="all", description="Delete a number of recent messages in this channel.")
@app_commands.describe(count="How many recent messages to delete (max 1000 recommended)")
async def purge_all(interaction: discord.Interaction, count: int):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    if count <= 0:
        await interaction.response.send_message("Please provide a positive number of messages to delete.", ephemeral=True)
        return

    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("This command can only be used in text channels or threads.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    messages = []
    async for msg in channel.history(limit=count):
        messages.append(msg)

    if not messages:
        await interaction.followup.send("No messages found to delete.", ephemeral=True)
        return

    per_user: Dict[str, int] = {}
    for msg in messages:
        name = f"{msg.author} ({msg.author.id})"
        per_user[name] = per_user.get(name, 0) + 1

    try:
        await channel.delete_messages(messages)
    except Exception as e:
        await interaction.followup.send(f"Failed to delete messages: `{e}`", ephemeral=True)
        return

    total_deleted = len(messages)
    lines = [f"{total_deleted} messages were removed.", ""]
    for user_name, amt in sorted(per_user.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{user_name} – {amt}")
    summary = "\n".join(lines)

    await channel.send(summary)
    await interaction.followup.send("Purge complete.", ephemeral=True)

bot.tree.add_command(purge)


@bot.tree.command(name="lock-down", description="Lock this channel so only mods+ can talk.")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def lock_down(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True)
        return
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("This command can only be used in text channels or threads.", ephemeral=True)
        return
    try:
        await channel.set_permissions(interaction.guild.default_role, send_messages=False)
    except Exception as e:
        await interaction.response.send_message(f"Failed to lock this channel: `{e}`", ephemeral=True)
        return
    await interaction.response.send_message("This channel has been **locked**. Only staff can talk now.", ephemeral=True)

@bot.command(name="lock")
@commands.guild_only()
async def lock_prefix(ctx: commands.Context):
    if ctx.guild is None or ctx.guild.id != MAIN_GUILD_ID:
        return
    if not isinstance(ctx.author, discord.Member) or not is_mod_or_admin(ctx.author):
        await ctx.reply("You must be a moderator or admin to use this command.", mention_author=False)
        return
    channel = ctx.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await ctx.reply("This command can only be used in text channels or threads.", mention_author=False)
        return
    try:
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
    except Exception as e:
        await ctx.reply(f"Failed to lock this channel: `{e}`", mention_author=False)
        return
    await ctx.reply("This channel has been **locked**. Only staff can talk now.", mention_author=False)


@bot.command(name="unban")
@commands.guild_only()
async def unban_prefix(ctx: commands.Context, user_id: str, *, reason: str = "Manual unban"):
    if ctx.guild is None or ctx.guild.id != MAIN_GUILD_ID:
        return

    if not isinstance(ctx.author, discord.Member) or not is_mod_or_admin(ctx.author):
        await ctx.reply("You must be a moderator or admin to use this command.", mention_author=False)
        return

    try:
        uid = int(user_id)
    except ValueError:
        await ctx.reply("Please provide a valid user ID.", mention_author=False)
        return

    if uid in permanent_bans:
        await ctx.reply(
            "This user has a **permanent ban** and cannot be unbanned via this command.",
            mention_author=False
        )
        return

    user = None
    try:
        user = await ctx.bot.fetch_user(uid)
    except Exception:
        user = ctx.bot.get_user(uid)

    try:
        await ctx.guild.unban(discord.Object(id=uid), reason=reason)
    except discord.NotFound:
        await ctx.reply("That user is not currently banned.", mention_author=False)
        return
    except Exception as e:
        await ctx.reply(f"Failed to unban user: `{e}`", mention_author=False)
        return

    permanent_bans.discard(uid)
    temp_bans.pop(uid, None)

    log_ch = get_log_channel()
    if log_ch is not None and ctx.guild.id == MAIN_GUILD_ID:
        case_id = get_next_case_id()
        now = now_utc()
        reason_text = reason if reason and reason.strip() not in ("Manual unban",) else f"No reason given, use !reason {case_id} <text> to add one"
        offender_user = user or ctx.bot.get_user(uid)
        offender_str = f"{uid} {offender_user.mention}" if offender_user else str(uid)
        log_embed = discord.Embed(title=f"unban | case {case_id}", color=discord.Color.green())
        log_embed.add_field(name="Offender:", value=offender_str, inline=False)
        log_embed.add_field(name="Reason:", value=reason_text, inline=False)
        log_embed.add_field(name="ID:", value=f"{uid} • {format_time(now)}", inline=False)
        try:
            await log_ch.send(embed=log_embed)
        except Exception:
            pass

    if user is not None:
        try:
            embed = discord.Embed(
                title="You Have Been Unbanned",
                description=f"[our main server]({MAIN_SERVER_INVITE})",
                color=discord.Color.green()
            )
            await user.send(embed=embed)
        except Exception:
            pass

    await ctx.reply(
        f"User with ID `{uid}` has been **unbanned**.\nReason: {reason}",
        mention_author=False
    )


@bot.tree.command(name="automod", description="Toggle automod on/off (authorized users only)")
@app_commands.describe(state="Turn automod on or off")
@app_commands.choices(state=[
    app_commands.Choice(name="on", value="on"),
    app_commands.Choice(name="off", value="off"),
])
async def automod(interaction: discord.Interaction, state: app_commands.Choice[str]):
    if interaction.user.id not in AUTOMOD_ALLOWED_USER_IDS:
        await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        return

    global AUTOMOD_ENABLED
    AUTOMOD_ENABLED = (state.value == "on")

    try:
        if AUTOMOD_ENABLED:
            if not temp_ban_watcher.is_running():
                temp_ban_watcher.start()
        else:
            if temp_ban_watcher.is_running():
                temp_ban_watcher.stop()
    except Exception:
        pass

    await interaction.response.send_message(
        f"Automod is now **{'ON' if AUTOMOD_ENABLED else 'OFF'}**.",
        ephemeral=True
    )


@bot.tree.command(name="false-ban", description="Unban a user due to a false ban and notify them.")
@app_commands.describe(user_id="ID of the user to unban (right click -> Copy ID)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def false_ban(interaction: discord.Interaction, user_id: str):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True)
        return
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("Please provide a valid user ID.", ephemeral=True)
        return
    if uid in permanent_bans:
        await interaction.response.send_message("This user has a **permanent ban** and cannot be unbanned via this command.", ephemeral=True)
        return

    user = None
    try:
        user = await interaction.client.fetch_user(uid)
    except Exception:
        user = interaction.client.get_user(uid)

    try:
        await interaction.guild.unban(discord.Object(id=uid), reason="False ban correction")
    except discord.NotFound:
        await interaction.response.send_message("That user is not currently banned.", ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"Failed to unban user: `{e}`", ephemeral=True)
        return

    permanent_bans.discard(uid)
    temp_bans.pop(uid, None)

    log_ch = get_log_channel()
    if log_ch is not None and interaction.guild.id == MAIN_GUILD_ID:
        now = now_utc()
        offender_user = user or interaction.client.get_user(uid)
        offender_str = f"{uid} {offender_user.mention}" if offender_user else str(uid)
        log_embed = discord.Embed(title="False ban", color=discord.Color.magenta())
        log_embed.add_field(name="Offender:", value=offender_str, inline=False)
        log_embed.add_field(name="Reason:", value="False ban – staff corrected the ban.", inline=False)
        log_embed.set_footer(text=format_time(now))
        try:
            await log_ch.send(embed=log_embed)
        except Exception:
            pass

    if user is not None:
        try:
            msg = ("A False Ban Was Issued! We are very sorry for the inconvenience,\n"
                   f"{MAIN_SERVER_INVITE}\nBest regards, Paper Rex Staff Team.")
            await user.send(msg)
        except Exception:
            pass

    await interaction.response.send_message(f"User with ID `{uid}` has been **unbanned** due to a false ban.", ephemeral=True)


# ---------- on_message_delete ----------
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    if message.guild is None or message.guild.id != MAIN_GUILD_ID:
        return
    log_ch = get_delete_log_channel()
    if log_ch is None:
        return
    channel_name = f"#{message.channel.name}" if isinstance(message.channel, discord.TextChannel) else "Unknown channel"
    content = message.content or "[no text]"
    created_at_text = format_time(now_utc())
    embed = discord.Embed(
        description=f"Message deleted in {channel_name}",
        color=discord.Color.red()
    )
    embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
    embed.add_field(name="Content", value=content[:1024], inline=False)
    embed.add_field(name="Message ID", value=str(message.id), inline=False)
    embed.set_footer(text=f"Deleted at {created_at_text}")
    if message.attachments:
        urls = "\n".join(att.url for att in message.attachments)
        embed.add_field(name="Attachments", value=urls[:1024], inline=False)
    try:
        await log_ch.send(embed=embed)
    except Exception:
        pass


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot:
        return
    if after.guild is None or after.guild.id != MAIN_GUILD_ID:
        return

    content = (after.content or "").lower()
    if any(bad in content for bad in BAD_WORDS):
        try:
            await after.delete()
        except Exception:
            return

        log_ch = get_delete_log_channel()
        if log_ch is not None:
            channel_name = f"#{after.channel.name}" if isinstance(after.channel, discord.TextChannel) else "Unknown"
            embed = discord.Embed(
                title="Auto-deleted edited message (bad word)",
                description=f"Deleted in {channel_name}",
                color=discord.Color.dark_red()
            )
            embed.add_field(name="Author", value=f"{after.author} ({after.author.id})", inline=False)
            embed.add_field(name="Content (after edit)", value=(after.content or "[no text]")[:1024], inline=False)
            embed.add_field(name="Message ID", value=str(after.id), inline=False)
            embed.set_footer(text=format_time(now_utc()))
            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass
        return

    try:
        suspicious = False
        if message_contains_suspicious_text(after.content):
            suspicious = True
        if not suspicious:
            for e in after.embeds:
                if embeds_contain_suspicious(e):
                    suspicious = True
                    break
        if not suspicious and attachments_or_embeds_have_images(after):
            if delete_images_always:
                suspicious = True
            else:
                if message_contains_suspicious_text(after.content) or message_has_suspicious_link(after):
                    suspicious = True
        if not suspicious and message_has_suspicious_link(after):
            suspicious = True

        if suspicious:
            try:
                await after.delete()
            except Exception:
                return

            log_ch = get_delete_log_channel()
            if log_ch is not None:
                channel_name = f"#{after.channel.name}" if isinstance(after.channel, discord.TextChannel) else "Unknown"
                embed = discord.Embed(
                    title="Auto-deleted edited message (suspicious)",
                    description=f"Deleted in {channel_name}",
                    color=discord.Color.dark_red()
                )
                embed.add_field(name="Author", value=f"{after.author} ({after.author.id})", inline=False)
                embed.add_field(name="Content (after edit)", value=(after.content or "[no text]")[:1024], inline=False)
                embed.add_field(name="Message ID", value=str(after.id), inline=False)
                if after.attachments:
                    urls = "\n".join(att.url for att in after.attachments)
                    embed.add_field(name="Attachments", value=urls[:1024], inline=False)
                embed.set_footer(text=format_time(now_utc()))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass
            return
    except Exception:
        pass


@tasks.loop(seconds=60)
async def temp_ban_watcher():
    now = now_utc()
    to_ban: List[int] = []
    for uid, end_time in list(temp_bans.items()):
        if now >= end_time:
            to_ban.append(uid)

    if not to_ban:
        return

    guild = bot.get_guild(MAIN_GUILD_ID)
    if guild is None:
        return

    for uid in to_ban:
        temp_bans.pop(uid, None)
        try:
            try:
                user = await bot.fetch_user(uid)
            except Exception:
                user = bot.get_user(uid)

            await guild.ban(
                discord.Object(id=uid),
                reason="Auto-ban after timeout expired (repeated offenses)",
                delete_message_seconds=0,
            )

            if user is not None:
                try:
                    await user.send(f"You have been banned from {SERVER_NAME} due to repeated rule violations.")
                except Exception:
                    pass

            log_ch = get_log_channel()
            if log_ch is not None:
                offender_str = f"{uid} {user.mention}" if user else str(uid)
                embed = discord.Embed(title="Auto-ban enacted", color=discord.Color.dark_red())
                embed.add_field(name="Offender:", value=offender_str, inline=False)
                embed.add_field(
                    name="Reason:",
                    value="Auto-ban after timeout expired (repeated bad-language offenses)",
                    inline=False,
                )
                embed.set_footer(text=format_time(now_utc()))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass
        except Exception:
            pass


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")

    # Sync slash commands for main guild only
    main_guild = discord.Object(id=MAIN_GUILD_ID)
    bot.tree.copy_global_to(guild=main_guild)
    await bot.tree.sync(guild=main_guild)

    try:
        if AUTOMOD_ENABLED and (not temp_ban_watcher.is_running()):
            temp_ban_watcher.start()
    except Exception:
        pass

    print("Slash commands synced for main guild.")


# ---------- Start bot ----------
bot.run(os.getenv("TOKEN"))

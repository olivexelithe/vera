import asyncio
import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("vera")


def required_int_env(name: str) -> int:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a Discord snowflake ID") from exc


def required_first_int_env(*names: str) -> int:
    for name in names:
        value = os.getenv(name)
        if value:
            try:
                return int(value)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be a Discord snowflake ID") from exc

    joined_names = " or ".join(names)
    raise RuntimeError(f"Missing required environment variable: {joined_names}")


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

WAITING_ROOM_ROLE_ID = required_int_env("WAITING_ROOM_ROLE_ID")
PVP_ROLE_ID = required_int_env("PVP_ROLE_ID")
NOVA_ROLE_ID = required_int_env("NOVA_ROLE_ID")
GUEST_PASS_ROLE_ID = required_int_env("GUEST_PASS_ROLE_ID")
UNDER_18_ROLE_ID = required_int_env("UNDER_18_ROLE_ID")
OVER_18_ROLE_ID = required_int_env("OVER_18_ROLE_ID")
ADMIN_ROLE_ID = required_int_env("ADMIN_ROLE_ID")
LOGISTICS_ROLE_ID = required_int_env("LOGISTICS_ROLE_ID")
ROLES_CHANNEL_ID = required_int_env("ROLES_CHANNEL_ID")
SWAMP_LOGS_CHANNEL_ID = required_first_int_env(
    "SWAMP_LOGS_CHANNEL_ID",
    "VERIFICATION_LOGS_CHANNEL_ID",
)

VERIFY_BUTTON_ID = "vera:open_verification"
AGE_UNDER_18_BUTTON_ID = "vera:age_under_18"
AGE_OVER_18_BUTTON_ID = "vera:age_over_18"
GROUP_PVP_UNDER_18_BUTTON_ID = "vera:group_pvp_under_18"
GROUP_PVP_OVER_18_BUTTON_ID = "vera:group_pvp_over_18"
GROUP_NOVA_UNDER_18_BUTTON_ID = "vera:group_nova_under_18"
GROUP_NOVA_OVER_18_BUTTON_ID = "vera:group_nova_over_18"
GROUP_GUEST_PASS_UNDER_18_BUTTON_ID = "vera:group_guest_pass_under_18"
GROUP_GUEST_PASS_OVER_18_BUTTON_ID = "vera:group_guest_pass_over_18"
REJECT_BUTTON_ID = "vera:reject"
APPLICANT_ID_RE = re.compile(r"\[(\d{15,25})\]$")
PULL_UP_RE = re.compile(
    r"^vera\s+pull\s+up\s+(.+?)(?:'s|’s)?\s+verification\s+details\s*$",
    re.IGNORECASE,
)


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True


class VeraBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(VerificationPanelView())
        self.add_view(StaffDecisionView())
        self.add_view(GroupDecisionView("under_18"))
        self.add_view(GroupDecisionView("over_18"))

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            LOGGER.info("Synced commands to guild %s", GUILD_ID)
        else:
            await self.tree.sync()
            LOGGER.info("Synced global commands")


bot = VeraBot()


def find_role(guild: discord.Guild, role_id: int) -> discord.Role:
    role = guild.get_role(role_id)
    if role is None:
        raise RuntimeError(f"Could not find role with ID {role_id}")
    return role


def roles_channel_mention(guild: discord.Guild) -> str:
    channel = guild.get_channel(ROLES_CHANNEL_ID)
    return channel.mention if channel else f"<#{ROLES_CHANNEL_ID}>"


def roles_channel_url(guild: discord.Guild) -> str:
    return f"https://discord.com/channels/{guild.id}/{ROLES_CHANNEL_ID}"


def display_name(member: discord.Member) -> str:
    return member.display_name or member.name


def username(member: discord.Member) -> str:
    discriminator = getattr(member, "discriminator", "0")
    if discriminator and discriminator != "0":
        return f"{member.name}#{discriminator}"
    return member.name


def global_name(member: discord.Member) -> str:
    return member.global_name or member.name


def member_has_staff_access(member: discord.Member) -> bool:
    staff_role_ids = {ADMIN_ROLE_ID, LOGISTICS_ROLE_ID}
    return any(role.id in staff_role_ids for role in member.roles)


async def send_not_authorised(destination: discord.abc.Messageable) -> None:
    await destination.send(
        "Sorry, you aren't authorised to do that. Please wait and a member of the team will verify you shortly."
    )


async def check_staff_interaction(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "This can only be used by staff inside the server.",
            ephemeral=True,
        )
        return False

    if not member_has_staff_access(interaction.user):
        await interaction.response.send_message(
            "Sorry, you aren't authorised to do that. Please wait and a member of the team will verify you shortly.",
            ephemeral=True,
        )
        return False

    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "These buttons only work inside a Vera verification thread.",
            ephemeral=True,
        )
        return False

    return True


def get_embed_field(embed: discord.Embed, name: str) -> str | None:
    for field in embed.fields:
        if field.name.lower() == name.lower():
            return field.value
    return None


async def find_verification_log(
    guild: discord.Guild,
    query: str,
) -> tuple[discord.Message, discord.Embed] | None:
    channel = guild.get_channel(SWAMP_LOGS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return None

    normalized_query = query.lower().strip()
    searchable_fields = (
        "Applicant",
        "Server name",
        "Username",
        "Global name",
        "User ID",
    )
    async for message in channel.history(limit=500, oldest_first=False):
        for embed in message.embeds:
            values = [get_embed_field(embed, field_name) or "" for field_name in searchable_fields]
            if any(normalized_query in value.lower() for value in values):
                return message, embed

    return None


def clone_verification_embed(source: discord.Embed, query: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Verification Details: {query}",
        color=source.color or discord.Color.blurple(),
    )

    for field_name in (
        "Applicant",
        "Server name",
        "Username",
        "Global name",
        "User ID",
        "Decision",
        "Reviewed by",
        "Thread",
        "Screenshot",
    ):
        value = get_embed_field(source, field_name)
        if value:
            embed.add_field(
                name=field_name,
                value=value,
                inline=field_name in {"Decision", "Reviewed by", "User ID"},
            )

    if source.image and source.image.url:
        embed.set_image(url=source.image.url)

    return embed


def applicant_id_from_thread(thread: discord.Thread) -> int | None:
    match = APPLICANT_ID_RE.search(thread.name)
    return int(match.group(1)) if match else None


def find_applicant_thread(
    channel: discord.TextChannel,
    applicant: discord.Member,
) -> discord.Thread | None:
    for thread in channel.threads:
        if applicant_id_from_thread(thread) == applicant.id:
            return thread
    return None


async def disable_decision_buttons(message: discord.Message) -> None:
    view = discord.ui.View.from_message(message, timeout=None)
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True
    await message.edit(view=view)


async def latest_applicant_screenshot(
    thread: discord.Thread,
    applicant_id: int,
) -> discord.Attachment | None:
    async for message in thread.history(limit=100, oldest_first=False):
        if message.author.id != applicant_id:
            continue

        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                return attachment

            filename = attachment.filename.lower()
            if filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                return attachment

    return None


async def post_verification_log(
    guild: discord.Guild,
    thread: discord.Thread,
    applicant: discord.Member,
    verifier: discord.Member,
    outcome: str,
    approved: bool,
) -> None:
    channel = guild.get_channel(SWAMP_LOGS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        LOGGER.warning("Swamp logs channel %s was not found", SWAMP_LOGS_CHANNEL_ID)
        return

    screenshot = await latest_applicant_screenshot(thread, applicant.id)
    embed = discord.Embed(
        title="Verification Complete" if approved else "Verification Rejected",
        color=discord.Color.green() if approved else discord.Color.red(),
    )
    embed.add_field(name="Applicant", value=f"{display_name(applicant)} ({applicant.id})", inline=False)
    embed.add_field(name="Server name", value=display_name(applicant), inline=True)
    embed.add_field(name="Username", value=username(applicant), inline=True)
    embed.add_field(name="Global name", value=global_name(applicant), inline=True)
    embed.add_field(name="User ID", value=str(applicant.id), inline=True)
    embed.add_field(name="Decision", value=outcome, inline=True)
    embed.add_field(name="Reviewed by", value=display_name(verifier), inline=True)
    embed.add_field(name="Thread", value=f"[Open verification thread]({thread.jump_url})", inline=False)

    file = None
    if screenshot:
        try:
            file = await screenshot.to_file(use_cached=True)
            embed.set_image(url=f"attachment://{file.filename}")
        except discord.HTTPException:
            LOGGER.exception("Could not attach verification screenshot %s", screenshot.url)
            embed.add_field(name="Screenshot", value=screenshot.url, inline=False)

    if not screenshot:
        embed.add_field(name="Screenshot", value="No screenshot attachment found.", inline=False)

    send_kwargs = {
        "embed": embed,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if file:
        send_kwargs["file"] = file

    await channel.send(**send_kwargs)


async def close_thread_after_delay(thread: discord.Thread, delay_seconds: int = 30) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await thread.edit(
            archived=True,
            locked=True,
            reason="Vera verification completed",
        )
    except discord.HTTPException:
        LOGGER.exception("Could not close verification thread %s", thread.id)


async def dm_roles_channel(member: discord.Member) -> bool:
    try:
        await member.send(
            "You have been verified in NOVA. Please choose your roles here: "
            f"{roles_channel_url(member.guild)}"
        )
        return True
    except discord.HTTPException:
        LOGGER.info("Could not DM roles channel link to %s", member)
        return False


async def finish_verification(
    interaction: discord.Interaction,
    role_id: int | None,
    outcome: str,
    age_role_id: int | None = None,
    age_label: str | None = None,
) -> None:
    assert interaction.guild is not None
    assert isinstance(interaction.user, discord.Member)
    assert isinstance(interaction.channel, discord.Thread)

    applicant_id = applicant_id_from_thread(interaction.channel)
    if applicant_id is None:
        await interaction.response.send_message(
            "I could not identify the applicant from this thread name.",
            ephemeral=True,
        )
        return

    applicant = interaction.guild.get_member(applicant_id)
    if applicant is None:
        await interaction.response.send_message(
            "I could not find that member in the server.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    waiting_role = find_role(interaction.guild, WAITING_ROOM_ROLE_ID)
    assigned_role = find_role(interaction.guild, role_id) if role_id else None
    age_role = find_role(interaction.guild, age_role_id) if age_role_id else None
    logged_outcome = f"{outcome} - {age_label}" if assigned_role and age_label else outcome

    if assigned_role:
        roles_to_add = [assigned_role]
        if age_role:
            roles_to_add.append(age_role)

        await applicant.add_roles(
            *roles_to_add,
            reason=f"Verified as {logged_outcome} by {interaction.user}",
        )
        await applicant.remove_roles(
            waiting_role,
            reason=f"Verification completed by {interaction.user}",
        )
        result = (
            f"{applicant.mention} has been verified as **{logged_outcome}** by "
            f"**{display_name(interaction.user)}**.\n\n"
            f"Next step: please head to {roles_channel_mention(interaction.guild)} "
            "to select your roles.\n\n"
            "This thread will close in 30 seconds."
        )
        dm_sent = await dm_roles_channel(applicant)
        if not dm_sent:
            result += (
                "\n\nI could not DM this member. They may have server DMs disabled, "
                "so please make sure they see the roles channel link here."
            )
    else:
        result = (
            f"{applicant.mention} was **rejected** by **{display_name(interaction.user)}**.\n\n"
            f"They will remain in the {waiting_role.mention} role.\n\n"
            "This thread will close in 30 seconds."
        )

    if interaction.message:
        await disable_decision_buttons(interaction.message)

    await interaction.followup.send(result, allowed_mentions=discord.AllowedMentions(users=True))
    await post_verification_log(
        interaction.guild,
        interaction.channel,
        applicant,
        interaction.user,
        logged_outcome,
        assigned_role is not None,
    )
    asyncio.create_task(close_thread_after_delay(interaction.channel))


class VerificationPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        custom_id=VERIFY_BUTTON_ID,
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used inside the server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Please use this from the verification channel.",
                ephemeral=True,
            )
            return

        existing_thread = find_applicant_thread(interaction.channel, interaction.user)
        if existing_thread:
            await interaction.response.send_message(
                f"You already have a verification thread open: {existing_thread.mention}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        thread = await interaction.channel.create_thread(
            name=f"vera-verification-{interaction.user.display_name} [{interaction.user.id}]",
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Vera verification started by {interaction.user}",
        )
        await thread.add_user(interaction.user)

        admin_role = find_role(interaction.guild, ADMIN_ROLE_ID)
        logistics_role = find_role(interaction.guild, LOGISTICS_ROLE_ID)

        embed = discord.Embed(
            title="Verification Started",
            description=(
                f"{interaction.user.mention}, please send us a recent screenshot "
                "of your game profile to be verified."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Staff can use the buttons below once the screenshot is reviewed.")

        await thread.send(
            content=f"{interaction.user.mention} {admin_role.mention} {logistics_role.mention}",
            embed=embed,
            view=StaffDecisionView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        await interaction.followup.send(
            f"I opened your private verification thread: {thread.mention}",
            ephemeral=True,
        )


class StaffDecisionView(discord.ui.View):
    def __init__(self, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = disabled

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_staff_interaction(interaction)

    async def ask_group(
        self,
        interaction: discord.Interaction,
        age_key: str,
        age_label: str,
    ) -> None:
        assert isinstance(interaction.user, discord.Member)

        await interaction.response.defer()

        if interaction.message:
            await disable_decision_buttons(interaction.message)

        embed = discord.Embed(
            title="Age Selected",
            description=(
                f"**{age_label}** selected by **{display_name(interaction.user)}**.\n\n"
                "Which group should this member be verified into?"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, view=GroupDecisionView(age_key))

    @discord.ui.button(
        label="Under 18",
        style=discord.ButtonStyle.primary,
        custom_id=AGE_UNDER_18_BUTTON_ID,
    )
    async def under_18(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.ask_group(interaction, "under_18", "Under 18")

    @discord.ui.button(
        label="Over 18",
        style=discord.ButtonStyle.primary,
        custom_id=AGE_OVER_18_BUTTON_ID,
    )
    async def over_18(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.ask_group(interaction, "over_18", "Over 18")

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        custom_id=REJECT_BUTTON_ID,
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await finish_verification(interaction, None, "Rejected")


class GroupDecisionView(discord.ui.View):
    def __init__(self, age_key: str, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.age_key = age_key
        self.age_role_id = UNDER_18_ROLE_ID if age_key == "under_18" else OVER_18_ROLE_ID
        self.age_label = "Under 18" if age_key == "under_18" else "Over 18"

        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            item.disabled = disabled
            if age_key == "under_18":
                item.custom_id = item.custom_id.replace("over_18", "under_18")
            else:
                item.custom_id = item.custom_id.replace("under_18", "over_18")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_staff_interaction(interaction)

    @discord.ui.button(
        label="PVP",
        style=discord.ButtonStyle.primary,
        custom_id=GROUP_PVP_UNDER_18_BUTTON_ID,
    )
    async def pvp(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await finish_verification(interaction, PVP_ROLE_ID, "PVP", self.age_role_id, self.age_label)

    @discord.ui.button(
        label="N0VA",
        style=discord.ButtonStyle.success,
        custom_id=GROUP_NOVA_UNDER_18_BUTTON_ID,
    )
    async def nova(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await finish_verification(interaction, NOVA_ROLE_ID, "N0VA", self.age_role_id, self.age_label)

    @discord.ui.button(
        label="Guest Pass",
        style=discord.ButtonStyle.secondary,
        custom_id=GROUP_GUEST_PASS_UNDER_18_BUTTON_ID,
    )
    async def guest_pass(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await finish_verification(
            interaction, GUEST_PASS_ROLE_ID, "Guest Pass", self.age_role_id, self.age_label
        )



@bot.event
async def on_ready() -> None:
    LOGGER.info("Logged in as %s", bot.user)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    match = PULL_UP_RE.match(message.content.strip())
    if not match:
        await bot.process_commands(message)
        return

    if not isinstance(message.author, discord.Member) or not member_has_staff_access(message.author):
        await send_not_authorised(message.channel)
        return

    query = match.group(1).strip().strip("\"'")
    result = await find_verification_log(message.guild, query)
    if result is None:
        await message.reply(
            f"I couldn't find verification details for **{query}** in swamp logs.",
            mention_author=False,
        )
        return

    log_message, source_embed = result
    embed = clone_verification_embed(source_embed, query)
    embed.add_field(name="Log message", value=log_message.jump_url, inline=False)
    await message.reply(embed=embed, mention_author=False)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    waiting_role = member.guild.get_role(WAITING_ROOM_ROLE_ID)
    if waiting_role is None:
        LOGGER.warning("Waiting room role %s was not found", WAITING_ROOM_ROLE_ID)
        return

    try:
        await member.add_roles(waiting_role, reason="New member joined; awaiting Vera verification")
    except discord.HTTPException:
        LOGGER.exception("Could not add waiting room role to %s", member)


async def post_verification_panel(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="Welcome to NOVA!",
        description="Please click the below button to verify.",
        color=discord.Color.brand_green(),
    )

    await interaction.channel.send(embed=embed, view=VerificationPanelView())
    await interaction.response.send_message(
        "Vera's verification panel is ready.",
        ephemeral=True,
    )


@bot.tree.command(name="vera", description="Post Vera's verification button panel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def vera(interaction: discord.Interaction) -> None:
    await post_verification_panel(interaction)


@bot.tree.command(name="setup_verification_panel", description="Post Vera's verification button panel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_verification_panel(interaction: discord.Interaction) -> None:
    await post_verification_panel(interaction)


@vera.error
@setup_verification_panel.error
async def setup_verification_panel_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need Manage Server permission to set up Vera's panel.",
            ephemeral=True,
        )
        return
    raise error


if not TOKEN:
    raise RuntimeError("Missing required environment variable: DISCORD_TOKEN")

bot.run(TOKEN)

import logging
import os
import re
from typing import Iterable

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


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

WAITING_ROOM_ROLE_ID = required_int_env("WAITING_ROOM_ROLE_ID")
PVP_ROLE_ID = required_int_env("PVP_ROLE_ID")
NOVA_ROLE_ID = required_int_env("NOVA_ROLE_ID")
ADMIN_ROLE_ID = required_int_env("ADMIN_ROLE_ID")
LOGISTICS_ROLE_ID = required_int_env("LOGISTICS_ROLE_ID")
ROLES_CHANNEL_ID = required_int_env("ROLES_CHANNEL_ID")

VERIFY_BUTTON_ID = "vera:open_verification"
VERIFY_PVP_BUTTON_ID = "vera:verify_pvp"
VERIFY_NOVA_BUTTON_ID = "vera:verify_nova"
REJECT_BUTTON_ID = "vera:reject"
APPLICANT_ID_RE = re.compile(r"\[(\d{15,25})\]$")


intents = discord.Intents.default()
intents.guilds = True
intents.members = True


class VeraBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(VerificationPanelView())
        self.add_view(StaffDecisionView())

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


def member_has_staff_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True

    staff_role_ids = {ADMIN_ROLE_ID, LOGISTICS_ROLE_ID}
    return any(role.id in staff_role_ids for role in member.roles)


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


async def fetch_role_members(role: discord.Role) -> set[discord.Member]:
    members = set(role.members)
    if members:
        return members

    try:
        async for member in role.guild.fetch_members(limit=None):
            if role in member.roles:
                members.add(member)
    except discord.HTTPException:
        LOGGER.warning("Could not fetch members for role %s", role.id)

    return members


async def add_members_to_thread(
    thread: discord.Thread,
    applicant: discord.Member,
    roles: Iterable[discord.Role],
) -> None:
    members_to_add = {applicant}
    for role in roles:
        members_to_add.update(await fetch_role_members(role))

    for member in members_to_add:
        try:
            await thread.add_user(member)
        except discord.HTTPException:
            LOGGER.warning("Could not add %s to thread %s", member, thread.id)


async def disable_decision_buttons(message: discord.Message) -> None:
    view = StaffDecisionView(disabled=True)
    await message.edit(view=view)


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

        admin_role = find_role(interaction.guild, ADMIN_ROLE_ID)
        logistics_role = find_role(interaction.guild, LOGISTICS_ROLE_ID)
        await add_members_to_thread(thread, interaction.user, (admin_role, logistics_role))

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
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used by staff inside the server.",
                ephemeral=True,
            )
            return False

        if not member_has_staff_access(interaction.user):
            await interaction.response.send_message(
                "Only the admin team and logistics team can use these buttons.",
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

    async def finish_verification(
        self,
        interaction: discord.Interaction,
        role_id: int | None,
        outcome: str,
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

        if assigned_role:
            await applicant.add_roles(
                assigned_role,
                reason=f"Verified as {outcome} by {interaction.user}",
            )
            await applicant.remove_roles(
                waiting_role,
                reason=f"Verification completed by {interaction.user}",
            )
            result = (
                f"{applicant.mention} has been verified as **{outcome}** by "
                f"{interaction.user.mention}.\n\n"
                f"Next step: please head to {roles_channel_mention(interaction.guild)} "
                "to select your roles."
            )
        else:
            result = (
                f"{applicant.mention} was **rejected** by {interaction.user.mention}.\n\n"
                f"They will remain in the {waiting_role.mention} role."
            )

        if interaction.message:
            await disable_decision_buttons(interaction.message)

        await interaction.followup.send(result, allowed_mentions=discord.AllowedMentions(users=True))

    @discord.ui.button(
        label="Verify PVP",
        style=discord.ButtonStyle.primary,
        custom_id=VERIFY_PVP_BUTTON_ID,
    )
    async def verify_pvp(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.finish_verification(interaction, PVP_ROLE_ID, "PVP")

    @discord.ui.button(
        label="Verify N0VA",
        style=discord.ButtonStyle.success,
        custom_id=VERIFY_NOVA_BUTTON_ID,
    )
    async def verify_nova(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.finish_verification(interaction, NOVA_ROLE_ID, "N0VA")

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
        await self.finish_verification(interaction, None, "Rejected")


@bot.event
async def on_ready() -> None:
    LOGGER.info("Logged in as %s", bot.user)


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


@bot.tree.command(name="setup_verification_panel", description="Post Vera's verification button panel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_verification_panel(interaction: discord.Interaction) -> None:
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

from __future__ import annotations

import discord
from discord.ext import commands
from discord import app_commands

from bot.constants import (
    challenger_role_id, accepting_team_invites_role_id, tortoise_guild_id,
    join_a_team_channel_id, teams_dashboard_message_id, server_link, bot_avatar_url
)
from bot.utils.checks import tortoise_bot_developer_only
from bot.utils.embed_handler import info, failure, success


class TicketReasonSelect(discord.ui.Select):
    """Dropdown menu for selecting the ticket/ban appeal reason."""

    def __init__(self, cog: "TortoiseDM"):
        options = [
            discord.SelectOption(
                label="Accidentally Selected 'I am Bot' Option",
                value="accidental_trap_victim",
                description="I accidentally selected 'I am Bot' option while joining.",
                emoji="🤖"
            ),
            discord.SelectOption(label="Unfair Ban", value="unfair_ban", description="I feel my ban was unjust.",
                                 emoji="⚖️"),
            discord.SelectOption(label="Apology / Second Chance", value="apology",
                                 description="I admit my mistake and want to apologize.", emoji="🙏"),
            discord.SelectOption(label="Compromised Account", value="compromised",
                                 description="My account was hacked when the violation occurred.", emoji="🛡️"),
            discord.SelectOption(label="Other Reason", value="other", description="Any other reason not listed above.",
                                 emoji="📝"),
        ]
        super().__init__(
            placeholder="Choose the reason for your appeal...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        reason = self.values[0]

        reason_mappings = {
            "accidental_trap_victim": "Accidentally Selected 'I am Bot' Option",
            "unfair_ban": "Unfair Ban Appeal",
            "apology": "Apology / Second Chance Request",
            "compromised": "Compromised Account Appeal",
            "other": "Other / Unspecified Reason"
        }
        chosen_reason = reason_mappings.get(reason, "Unspecified Reason")

        self.disabled = True

        if reason == "accidental_trap_victim":
            await interaction.response.edit_message(view=self.view)

            is_banned = await self.cog.bot.progression_manager.is_auto_banned(user_id=user.id,
                                                                              guild_id=tortoise_guild_id)

            if is_banned:
                guild = self.cog.bot.get_guild(tortoise_guild_id)

                try:
                    ban_entry = await guild.fetch_ban(discord.Object(id=user.id))
                    target_user = ban_entry.user

                    await guild.unban(target_user, reason="Auto unbanned via Honeypot Trap Appeal panel.")
                    await self.cog.bot.safe_send(
                        target_user,
                        content=server_link,
                        embed=info(
                            "You have been unbanned in Tortoise Community\n"
                            "Please use the invite link to rejoin the server\n",
                            self.cog.bot.user,
                            "Ban Lifted!",
                            "Welcome back to Tortoise Programming Community!",
                        )
                    )

                    await self.cog.bot.progression_manager.set_ban_status(user_id=user.id,
                                                                          guild_id=tortoise_guild_id,
                                                                          status=False)

                    await interaction.followup.send(f"✅ Successfully unbanned. You may rejoin!", ephemeral=True)

                except discord.NotFound:
                    await interaction.followup.send("You are not currently recorded on the server ban list.",
                                                    ephemeral=True)
                except discord.HTTPException:
                    await interaction.followup.send(
                        "❌ Something went wrong while attempting to unban. Try again later.", ephemeral=True)
            else:
                await interaction.followup.send(
                    embed=failure(
                        "Our records indicate you weren't banned by the automated bot trap.\nPlease select a different appeal reason."),
                    ephemeral=True
                )

        else:
            await interaction.response.edit_message(
                content="⏳ Processing your request and opening a ticket...",
                view=self.view
            )

            try:
                embed = info(
                    f"Your ban appeal request is logged.\n"
                    f"**Reason:** {chosen_reason}\n"
                    "Please wait for a moderator to respond.\n\n",
                    self.cog.bot.user,
                    "Ticket Created!"
                )
                embed.set_footer(text="NOTE: Please remain in this server until this ticket is closed.")
                await user.send(embed=embed)
            except discord.HTTPException:
                await interaction.followup.send(
                    "❌ I couldn't send you a Direct Message. Please enable DMs from server members and try again.",
                    ephemeral=True
                )
                return

            await self.cog.create_mod_mail(user, reason=chosen_reason, source="panel", ping=False)


class TicketReasonView(discord.ui.View):
    """Temporary ephemeral view containing the reason dropdown."""

    def __init__(self, cog: "TortoiseDM"):
        super().__init__(timeout=60)
        self.add_item(TicketReasonSelect(cog))


class ModMailStartView(discord.ui.View):
    """Persistent button view for Mod mail ticket creation."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="📩",
        custom_id="tortoise_modmail_panel",
    )
    async def start_modmail(self, interaction: discord.Interaction, button: discord.ui.Button):

        bot = interaction.client
        cog: "TortoiseDM" = bot.get_cog("TortoiseDM")
        user = interaction.user

        if cog.is_any_session_active(user.id):
            await interaction.response.send_message(
                "You already have an active session.",
                ephemeral=True
            )
            return

        if cog.cool_down.is_on_cool_down(user.id):
            msg = f"You are on cooldown. Retry after {cog.cool_down.retry_after(user.id)}s."
            await interaction.response.send_message(embed=failure(msg), ephemeral=True)
            return

        cog.cool_down.add_to_cool_down(user.id)

        await interaction.response.send_message(
            "📩 Please select the reason for your ban appeal below:",
            view=TicketReasonView(cog),
            ephemeral=True
        )

class NotifyButton(discord.ui.View):
    """Persistent button view for challenge notifications."""

    def __init__(self):
        super().__init__(timeout=None)  # persistent

    @discord.ui.button(
        label="Notify me",
        style=discord.ButtonStyle.primary,
        emoji="🔔",
        custom_id="challenge_notify_button",
    )
    async def notify_me(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button can only be used in a server.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(challenger_role_id)
        if role is None:
            await interaction.response.send_message(
                "Notification role is not configured correctly.",
                ephemeral=True,
            )
            return

        member = interaction.user
        assert isinstance(member, discord.Member)

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Challenge notifications opt-out")
            except discord.Forbidden:
                await interaction.response.send_message("No permission | Contact Administrator", ephemeral=True)
            await interaction.response.send_message(
                "🔕 You will no longer receive challenge notifications.",
                ephemeral=True,
            )
        else:
            try:
                await member.add_roles(role, reason="Challenge notifications opt-in")
            except discord.Forbidden:
                await interaction.response.send_message("No permission | Contact Administrator", ephemeral=True)
            await interaction.response.send_message(
                "🔔 You will now receive challenge notifications!",
                ephemeral=True,
            )


class TeamInvitesButton(discord.ui.View):
    """Persistent button view for team invite notifications."""

    def __init__(self):
        super().__init__(timeout=None)  # persistent

    @discord.ui.button(
        label="Click Me",
        style=discord.ButtonStyle.primary,
        emoji="📨",
        custom_id="team_invite_notify_button",
    )
    async def notify_me(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        role = interaction.guild.get_role(accepting_team_invites_role_id)
        member = interaction.user

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Team Invites opt-out")
            except discord.Forbidden:
                await interaction.response.send_message("No permission | Contact Administrator", ephemeral=True)
            await interaction.response.send_message(
                "🔕 You will no longer receive team invites.",
                ephemeral=True,
            )
        else:
            try:
                await member.add_roles(role, reason="Team Invites opt-in")
            except discord.Forbidden:
                await interaction.response.send_message("No permission | Contact Administrator", ephemeral=True)
            await interaction.response.send_message(
                "🔔 You will now receive team invites!",
                ephemeral=True,
            )


class ButtonUtility(commands.Cog):
    """Cog for posting challenge notification opt-in messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Register persistent view on startup
        self.bot.add_view(NotifyButton())
        self.bot.add_view(ModMailStartView())
        self.bot.add_view(TeamInvitesButton())


    @app_commands.command(
        name="post_challenge_notification",
        description="Post the challenge notification opt-in message.",
    )
    @app_commands.check(tortoise_bot_developer_only)
    async def post_challenge_notification(
        self,
        interaction: discord.Interaction,
    ):
        embed = discord.Embed(
            title="Challenge Notifications",
            description=(
                "Click here to get notified whenever a new challenge is posted."
            ),
            color=discord.Color.blurple(),
        )

        embed.set_footer(text="You can opt out anytime by clicking the button again.")

        await interaction.response.send_message(
            embed=embed,
            view=NotifyButton(),
        )


    @app_commands.command(
        name="post_modmail_panel",
        description="Post the mod mail contact panel."
    )
    @app_commands.check(tortoise_bot_developer_only)
    async def post_panel(self, interaction: discord.Interaction, channel_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(channel_id)
        except ValueError:
            await interaction.followup.send("❌ Please provide a valid numerical Channel ID.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(target_id)

        await channel.purge(limit=1)

        embed = discord.Embed(
            title="Ban appeal",
            description="Use the button below to create a ticket and submit a ban appeal.",
            color=discord.Color.dark_green()
        )

        embed.set_footer(text="Tortoise Programming Community", icon_url=bot_avatar_url)

        await channel.send(
            embed=embed,
            view=ModMailStartView()
        )
        await interaction.followup.send(
            embed=success("Done")
        )

    @app_commands.command(
        name="post_team_invites_notification",
        description="Post the team invites opt-in message.",
    )
    @app_commands.check(tortoise_bot_developer_only)
    async def post_team_invites_notification(
        self,
        interaction: discord.Interaction,
    ):
        embed = discord.Embed(
            title="Receive Team Invites",
            description=(
                "Click here to receive team invites from team leads.\n\n"
                "Teams are designed for focused DSA preparation with like-minded people, preferably in the same timezone.\n"
                "This may include organized group calls, discussions, and collaboration.\n\n"
                f"**All Teams: **: https://discord.com/channels/"
                f"{tortoise_guild_id}/{join_a_team_channel_id}/{teams_dashboard_message_id}\n\n"
            ),
            color=discord.Color.blurple(),
        )

        embed.set_footer(text="You can opt out anytime by clicking the button again.")

        await interaction.response.send_message(
            embed=embed,
            view=TeamInvitesButton(),
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(ButtonUtility(bot))

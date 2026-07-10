import logging
import datetime
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.errors import HTTPException

from bot import constants
from bot.utils.misc import get_utc_time_until
from bot.utils.checks import check_if_it_is_tortoise_guild
from bot.utils.embed_handler import failure, welcome, footer_embed, info


logger = logging.getLogger(__name__)


ALIAS_MAP = {}
for num, rule in constants.RULES.items():
    for alias in rule["aliases"]:
        ALIAS_MAP[alias.lower()] = num


class TortoiseServer(commands.Cog):
    """These commands will only work in the tortoise discord server."""
    def __init__(self, bot):
        self.bot = bot
        self._tortoise_guild = None
        self._new_member_role = None
        self._log_channel = None

    @property
    def tortoise_guild(self):
        if self._tortoise_guild is None:
            self._tortoise_guild = self.bot.get_guild(constants.tortoise_guild_id)
        return self._tortoise_guild

    @property
    def new_member_role(self):
        if self._new_member_role is None:
            self._new_member_role = self.tortoise_guild.get_role(constants.new_member_role_id)
        return self._new_member_role

    @property
    def log_channel(self):
        if self._log_channel is None:
            self._log_channel = self.bot.get_channel(constants.bot_log_channel_id)
        return self._log_channel


    async def _new_member_register_in_database(self, member: discord.Member):
        logger.info(f"New member {member} does not exist in database, adding now.")
        await self.bot.api_client.insert_new_member(member)
        await member.add_roles(self.new_member_role)
        await self.log_channel.send(embed=welcome(member))
        dm_msg = (
            "Welcome to Tortoise Community!\n\n"
            f"By joining the server you agree to our [rules]({constants.rules_url}).\n"
            f"We hope you enjoy your stay!"
        )
        await member.send(embed=footer_embed(dm_msg, "Welcome"))


    @tasks.loop(hours=24)
    async def remove_new_member_role(self):
        utc0 = datetime.timezone(offset=datetime.timedelta(hours=0))
        for member in self.new_member_role.members:
            if member.joined_at is None:
                continue

            join_duration = abs(datetime.datetime.now(tz=utc0).date() - member.joined_at.date())
            if join_duration.days >= 10:
                try:
                    await member.remove_roles(self.new_member_role, reason="New member role expired")
                except HTTPException:
                    logger.warning(f"Bot could't remove new member role from {member} {member.id}")


    @commands.command(enabled=False)
    @commands.check(check_if_it_is_tortoise_guild)
    async def deadline(self, ctx):
        """Shows how much time until Code Jam is over."""
        try:
            time_until_string = get_utc_time_until(year=2020, month=11, day=17, hour=23, minute=59, second=59)
            await ctx.send(embed=info(time_until_string, ctx.me, title="Code Jam ends in:"))
        except ValueError:
            await ctx.send(embed=info("Code Jam is over!", member=ctx.me, title="Finished"))


    @commands.command(enabled=False)
    @commands.check(check_if_it_is_tortoise_guild)
    async def submit(self, ctx):
        """Initializes process of submitting code for event."""
        fake_payload = SimpleNamespace()
        fake_payload.user_id = ctx.author.id
        fake_payload.emoji = self.bot.get_emoji(constants.event_emoji_id)
        await self.bot.get_cog("TortoiseDM").on_raw_reaction_add_helper(fake_payload)
        await ctx.send(embed=info(
            "Check your DMs.\n"
            "Note: if you already have active DM option nothing will happen.",
            ctx.me)
        )

    @app_commands.command(
        name="rules",
        description="Show all rules or a specific rule using alias"
    )
    @app_commands.describe(alias="Optional rule alias (e.g. dm, nsfw, ping, tos)")
    async def rules(self, interaction: discord.Interaction, alias: str | None = None):

        if alias:
            key = alias.lower().strip()
            rule_num = ALIAS_MAP.get(key)

            if not rule_num:
                await interaction.response.send_message(
                    embed=failure(f"Unknown rule alias: `{alias}`"),
                    ephemeral=True
                )
                return

            rule = constants.RULES[rule_num]

            embed = discord.Embed(
                title=f"Rule {rule_num}: {rule['title']}",
                color=discord.Color.dark_grey()
            )

            embed.description = (
                f"{rule['text']}\n"
            )
            embed.set_footer(text=f"Aliases: [{', '.join(rule['aliases'])}]")
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(
            title="Tortoise - Programming Community Rules",
            color=discord.Color.dark_grey()
        )

        blocks = []
        for num in sorted(constants.RULES.keys()):
            rule = constants.RULES[num]
            block = (
                f"**{num}. {rule['title']}**"
                f"{rule['text']}\n"
                f"[aliases: {', '.join(rule['aliases'])}]"
            )
            blocks.append(block)

        embed.description = "\n\n".join(blocks) + "\n\n"
        embed.set_footer(text="Tortoise Community")
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.remove_new_member_role.start()


async def setup(bot):
    await bot.add_cog(TortoiseServer(bot))

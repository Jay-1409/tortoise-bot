import logging

from discord.ext import commands
from discord import Member, Embed, Forbidden

from bot import constants
from bot.bot import Bot
from bot.api_client import ResponseCodeError
from bot.utils.converters import DatabaseMember
from bot.utils.embed_handler import failure, goodbye, info
from bot.utils.checks import check_if_it_is_tortoise_guild


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TortoiseAPI(commands.Cog):
    """Commands using Tortoise API"""
    def __init__(self, bot: Bot):
        self.bot: Bot = bot
        self.system_log_channel = bot.get_channel(constants.bot_log_channel_id)

    @commands.command()
    @commands.has_guild_permissions(manage_roles=True, manage_messages=True)
    @commands.check(check_if_it_is_tortoise_guild)
    async def is_verified(self, ctx, member: DatabaseMember):
        try:
            response = await self.bot.api_client.is_verified(member)
        except ResponseCodeError as e:
            msg = f"Something went wrong, got response status {e.status}.\nDoes the member exist?"
            await ctx.send(embed=failure(msg))
        else:
            await ctx.send(embed=info(f"Verified: {response}", ctx.me, title=f"{member}"))

    @commands.command()
    @commands.has_guild_permissions(administrator=True)
    @commands.check(check_if_it_is_tortoise_guild)
    async def show_data(self, ctx, member: DatabaseMember):
        try:
            data = await self.bot.api_client.get_member_data(member)
        except ResponseCodeError as e:
            msg = f"Something went wrong, got response status {e.status}.\nDoes the member exist?"
            await ctx.send(embed=failure(msg))
        else:
            pretty = "\n".join(f"{key}:{value}\n" for key, value in data.items())
            await ctx.send(embed=info(pretty, ctx.me, "Member data"))

    @commands.Cog.listener()
    @commands.check(check_if_it_is_tortoise_guild)
    async def on_member_remove(self, member: Member):
        logger.debug(f"Member {member} left, updating database accordingly.")
        await self.bot.api_client.member_left(member)
        await self.system_log_channel.send(embed=goodbye(member))

    async def _dm_member(self, user_id, embed: Embed):
        try:
            user = self.bot.get_user(user_id)
            await user.send(embed=embed)
        except Forbidden:
            pass

async def setup(bot):
    await bot.add_cog(TortoiseAPI(bot))

from discord.ext import commands


class Leaderboard(commands.Cog):
    """Compatibility cog.

    Challenge points and leaderboard slash commands now live under
    `/challenge ...` in the Challenge cog.
    """


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard())

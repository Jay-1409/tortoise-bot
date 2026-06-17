import os
import time

import psutil
import discord
from discord.ext import commands
from discord import app_commands

from bot.utils.message_handler import RemovableMessage
from bot.utils.embed_handler import info
from bot.constants import embed_space


class Miscellaneous(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.process = psutil.Process(os.getpid())
        self.countdown_started = False

    @app_commands.command(name="members")
    async def members(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=info(f"**Member count:** {interaction.guild.member_count}", interaction.guild.me, "")
        )

    @app_commands.command(name="ping")
    async def ping(self, interaction: discord.Interaction):
        start = time.perf_counter()
        await interaction.response.send_message(embed=info("Pong!", interaction.guild.me))
        end = time.perf_counter()
        duration = (end - start) * 1000
        await interaction.edit_original_response(
            embed=info(f":ping_pong: {duration:.2f}ms", interaction.guild.me, "Pong!")
        )

    @app_commands.command(name="stats")
    @app_commands.checks.cooldown(1, 10)
    async def stats(self, interaction: discord.Interaction):
        bot_ram_usage = self.process.memory_full_info().rss / 1024 ** 2
        bot_ram_usage = f"{bot_ram_usage:.2f} MB"
        bot_ram_usage_field = self.construct_load_bar_string(
            self.process.memory_percent(), bot_ram_usage
        )

        virtual_memory = psutil.virtual_memory()
        server_ram_usage = f"{virtual_memory.used/1024/1024:.0f} MB"
        server_ram_usage_field = self.construct_load_bar_string(
            virtual_memory.percent, server_ram_usage
        )

        cpu_count = psutil.cpu_count()

        bot_cpu_usage = self.process.cpu_percent()
        if bot_cpu_usage > 100:
            bot_cpu_usage = bot_cpu_usage / cpu_count
        bot_cpu_usage_field = self.construct_load_bar_string(bot_cpu_usage)

        server_cpu_usage = psutil.cpu_percent()
        if server_cpu_usage > 100:
            server_cpu_usage = server_cpu_usage / cpu_count
        server_cpu_usage_field = self.construct_load_bar_string(server_cpu_usage)

        io_counters = self.process.io_counters()
        io_read_bytes = f"{io_counters.read_bytes/1024/1024:.3f}MB"
        io_write_bytes = f"{io_counters.write_bytes/1024/1024:.3f}MB"

        field_content = (
            f"**Bot RAM usage:**{embed_space*7}{bot_ram_usage_field}\n"
            f"**Server RAM usage:**{embed_space}{server_ram_usage_field}\n"
            f"**Bot CPU usage:**{embed_space*9}{bot_cpu_usage_field}\n"
            f"**Server CPU usage:**{embed_space*3}{server_cpu_usage_field}\n"
            f"**IO (r/w):** {io_read_bytes} / {io_write_bytes}\n"
        )

        embed = info("", interaction.guild.me, title="")
        embed.set_author(name="Tortoise BOT", icon_url=interaction.guild.me.avatar.url)
        embed.add_field(name="Bot Stats", value=field_content)
        embed.set_footer(text="Tortoise Community")

        await interaction.response.send_message(embed=embed)

    @staticmethod
    def construct_load_bar_string(percent: int, suffix_message: str = None, size: int = 10):
        limiters = "|"
        element_emtpy = "▱"
        element_full = "▰"
        constructed = [limiters]

        if size < 8:
            size = 8
        if percent > 100:
            percent = 100

        progress = int(round(percent / size))

        for _ in range(0, progress):
            constructed.append(element_full)
        for _ in range(progress, size):
            constructed.append(element_emtpy)

        constructed.append(limiters)
        constructed = "".join(constructed)

        if suffix_message is None:
            constructed = f"{constructed} {percent:.2f}%"
        else:
            constructed = f"{constructed} {suffix_message}"

        return constructed

    @app_commands.command(name="tag", description="Select a community resource tag directly from your chat menu.")
    @app_commands.choices(name=[
        app_commands.Choice(name="Ask", value="ask"),
        app_commands.Choice(name="Markdown", value="markdown"),
        app_commands.Choice(name="Run Help", value="run_help"),
        app_commands.Choice(name="Zen", value="zen"),
        app_commands.Choice(name="Add to Issues", value="add_to_issues")
    ])
    async def tag(self, interaction: discord.Interaction, name: app_commands.Choice[str]):
        value = name.value

        if value == "ask":
            content = (
                "Don't ask to ask, just ask.\n\n"
                " • You will have much higher chances of getting an answer\n"
                " • We can skip the whole process of actually getting the question out of you thus you will get "
                "answer faster\n\n"
                "For more info visit https://dontasktoask.com/"
            )
            embed = info(content, interaction.guild.me, "")
            await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()
            await RemovableMessage.create_instance(self.bot, message, interaction.user)

        elif value == "markdown":
            content = (
                "You can format your code by using markdown like this:\n\n"
                "\\`\\`\\`python\n"
                "print('Hello world')\n"
                "\\`\\`\\`\n\n"
                "This would give you:\n"
                "```python\n"
                "print('Hello world')```\n"
                "**Video explanation:**\n"
            )
            embed = info(
                content, interaction.guild.me, "",
                "Note: The character ` is not a quote but a backtick."
            )
            embed.set_image(url="https://lairesit.sirv.com/Tortoise/howto.gif")
            await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()
            await RemovableMessage.create_instance(self.bot, message, interaction.user)

        elif value == "run_help":
            content = (
                "# How to Run Code using the bot\n\n"
                "Run code by sending a message that starts with `/run` followed by a fenced code block.\n\n"
                "**Format:**\n\n"
                "/run\n\\`\\`\\`<language>\n"
                "print(1 + 1)\n"
                "\\`\\`\\`\n\n"
                "Language support: **python**, **javascript**, **java** (**py**,**js** also works)\n\n"
                "### Examples\n"
                "**Python:**\n\n"
                "/run ```python\n"
                "print(1 + 1)\n"
                "```\n"
                "**JavaScript:**\n\n"
                "/run ```javascript\n"
                "console.log(1 + 1)\n"
                "```\n"
                "**Java:**\n\n"
                "/run ```java\n"
                "public class Main {\n"
                "    public static void main(String[] args) {\n"
                "        System.out.println(1 + 1);\n"
                "    }\n"
                "}\n"
                "```\n\n"
                "**Video Explanation:**\n"
            )
            embed = info(
                content, interaction.guild.me, "",
                "You can edit your message within 2 minutes to re-run the code automatically."
            )
            embed.set_image(url="https://lairesit.sirv.com/Tortoise/howtoruncode.gif")
            await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()
            await RemovableMessage.create_instance(self.bot, message, interaction.user)

        elif value == "zen":
            zen = """
                Beautiful is better than ugly.
                Explicit is better than implicit.
                Simple is better than complex.
                Complex is better than complicated.
                Flat is better than nested.
                Sparse is better than dense.
                Readability counts.
                Special cases aren't special enough to break the rules.
                Although practicality beats purity.
                Errors should never pass silently.
                Unless explicitly silenced.
                In the face of ambiguity, refuse the temptation to guess.
                There should be one-- and preferably only one --obvious way to do it.
                Although that way may not be obvious at first unless you're Dutch.
                Now is better than never.
                Although never is often better than *right* now.
                If the implementation is hard to explain, it's a bad idea.
                If the implementation is easy to explain, it may be a good idea.
                Namespaces are one honking great idea -- let's do more of those!
            """
            await interaction.response.send_message(
                embed=info(zen, interaction.guild.me, title="The Zen of Python, by Tim Peters")
            )

        elif value == "add_to_issues":
            msg = r"""
                ░█████╗░██████╗░██████╗░  ████████╗░█████╗░  ██╗░██████╗░██████╗██╗░░░██╗███████╗░██████╗
                ██╔══██╗██╔══██╗██╔══██╗  ╚══██╔══╝██╔══██╗  ██║██╔════╝██╔════╝██║░░░██║██╔════╝██╔════╝
                ███████║██║░░██║██║░░██║  ░░░██║░░░██║░░██║  ██║╚█████╗░╚█████╗░██║░░░██║█████╗░░╚█████╗░
                ██╔══██║██║░░██║██║░░██║  ░░░██║░░░██║░░██║  ██║░╚═══██╗░╚═══██╗██║░░░██║██╔══╝░░░╚═══██╗
                ██║░░██║██████╔╝██████╔╝  ░░░██║░░░╚█████╔╝  ██║██████╔╝██████╔╝╚██████╔╝███████╗██████╔╝
                ╚═╝░░╚═╝╚═════╝░╚═════╝░  ░░░╚═╝░░░░╚════╝░  ╚═╝╚═════╝░╚═════╝░░╚═════╝░╚══════╝╚═════╝░            
                """
            await interaction.response.send_message(f"```\n{msg}```")


async def setup(bot):
    cog = Miscellaneous(bot)
    await bot.add_cog(cog)

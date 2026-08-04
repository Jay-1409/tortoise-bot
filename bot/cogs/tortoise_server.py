"""
Credits
Welcome info component is directly inspired from DuckyBot in ente.io support server
Repository: https://github.com/brog-io/DuckyBot/blob/main/cogs/server_manager.py
"""


import logging
import datetime
import re
from types import SimpleNamespace
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.errors import HTTPException
from discord.http import Route
from discord.ui import Button, View

from bot import constants
from bot.utils.misc import get_utc_time_until
from bot.utils.checks import check_if_it_is_tortoise_guild
from bot.utils.embed_handler import failure, welcome, footer_embed, info

logger = logging.getLogger(__name__)

ALIAS_MAP = {}
for num, rule in constants.RULES.items():
    for alias in rule["aliases"]:
        ALIAS_MAP[alias.lower()] = num

TARGET_CHANNEL_ID = constants.project_showcase_channel_id
AUTO_THREAD_REACTIONS = ["⭐"]

MESSAGE_FLAG_IS_COMPONENTS_V2 = 1 << 15

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def is_image_attachment(attachment: discord.Attachment) -> bool:
    if getattr(attachment, "content_type", None):
        return attachment.content_type.startswith("image/")
    return attachment.url.lower().endswith(IMAGE_EXTENSIONS)


def _safe_text(s: Optional[str], max_len: int = 1800) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


class MessageLinkButton(Button):
    def __init__(self, message_url: str):
        super().__init__(
            style=discord.ButtonStyle.link,
            label="Open Message",
            url=message_url,
        )


class TortoiseServer(commands.Cog):
    """These commands will only work in the tortoise discord server."""
    def __init__(self, bot):
        self.bot = bot
        self._tortoise_guild = None
        self._new_member_role = None
        self._log_channel = None

        self.message_link_pattern = re.compile(
            r"https?:\/\/(?:.*\.)?discord\.com\/channels\/(\d+)\/(\d+)\/(\d+)"
        )
        self.target_channel_id = TARGET_CHANNEL_ID
        self.reactions = AUTO_THREAD_REACTIONS

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
                f"**{num}. {rule['title']}**\n"
                f"**{num}. {rule['title']}**"
                f"{rule['text']}\n"
                f"[aliases: {', '.join(rule['aliases'])}]"
            )
            blocks.append(block)

        embed.description = "\n\n".join(blocks) + "\n\n"
        embed.set_footer(text="Tortoise Community")
        await interaction.response.send_message(embed=embed)

    async def _send_components_v2_message(
            self,
            channel_id: int,
            *,
            components: list,
            allowed_mentions: Optional[dict] = None,
            message_reference: Optional[dict] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "flags": MESSAGE_FLAG_IS_COMPONENTS_V2,
            "components": components,
        }

        if allowed_mentions is not None:
            payload["allowed_mentions"] = allowed_mentions

        if message_reference is not None:
            payload["message_reference"] = message_reference

        route = Route("POST", "/channels/{channel_id}/messages", channel_id=channel_id)
        return await self.bot.http.request(route, json=payload)

    def _welcome_components_v2(self) -> list:
        container = {
            "type": 17,
            "accent_color": constants.default_color,
            "spoiler": False,
            "components": [
                {
                    "type": 12,
                    "items": [{"media": {"url": constants.info_banner_url}}],
                },
                {"type": 14, "spacing": 2, "divider": True},
                {
                    "type": 10,
                    "content": (
                        "# Welcome to the Tortoise Community!\n"
                        "Explore our coding community and projects:\n"
                        f"**Challenges**: Compete and learn in <#{constants.challenges_channel_id}>.\n"
                        f"**Projects**: Show off your creations in <#{constants.project_showcase_channel_id}>.\n"
                        f"**LeetCode**: Discuss and solve problems in <#{constants.leetcode_channel_id}>.\n"
                        f"**Teams**: Join a DSA study group in <#{constants.join_a_team_channel_id}>.\n\n"
                        "We’re glad to have you here! 🐢"
                    ),
                },
                {"type": 14, "spacing": 1, "divider": True},
                {
                    "type": 10,
                    "content": (
                        "## Community Guidelines\n"
                        "• **Just Ask**: Do not ask to ask. Just ask!\n"
                        "• **Respect Everyone**: No abusive slurs. Respect all members and staff.\n"
                        "• **No Advertisement**: No unapproved advertising or requests for paid work.\n"
                        "• **Relevancy**: Keep discussions relevant to the channel topics.\n"
                        "• **No DMing**: Do not DM members without getting their permission first."
                    ),
                },
            ],
        }

        action_row = {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": "Rules",
                    "custom_id": "Rules",
                    "emoji": {"name": "rules", "id": str(constants.rules_emoji_id)},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Roles",
                    "custom_id": "Roles",
                    "emoji": {"name": "roles", "id": str(constants.roles_emoji_id)},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Channels",
                    "custom_id": "Channels",
                    "emoji": {"name": "channels", "id": str(constants.channels_emoji_id)},
                },
                {
                    "type": 2,
                    "style": 5,
                    "label": "Website",
                    "url": constants.website_url,
                },
                {
                    "type": 2,
                    "style": 5,
                    "label": "Online Compiler",
                    "url": constants.online_compiler_link,
                },
            ],
        }

        return [container, action_row]

    @app_commands.command(name="welcome")
    @app_commands.default_permissions(administrator=True)
    async def send_welcome(self, interaction: discord.Interaction):
        await self._send_components_v2_message(
            constants.rules_channel_id,
            components=self._welcome_components_v2(),
            allowed_mentions={"parse": []},
        )
        await interaction.response.send_message(
            "Welcome message sent.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = (interaction.data or {}).get("custom_id")

        if custom_id == "Rules":
            rules_desc = "# Server Rules\n\n"
            for rule_num, rule_info in constants.RULES.items():
                rules_desc += f"**{rule_num}. {rule_info['title']}**{rule_info['text']}\n\n"

            rules_embed = discord.Embed(
                description=rules_desc.strip(),
                color=constants.default_color,
            )
            await interaction.response.send_message(
                embed=rules_embed,
                ephemeral=True,
            )

        elif custom_id == "Roles":
            roles_embed = discord.Embed(
                description=(
                    "# Server Roles\n"
                    "Roles in this server recognize activity, contribution, and trust within the community. "
                    "Some roles are earned automatically through participation, some are awarded through "
                    "community nominations, and a few are granted directly for special achievements.\n\n"

                    "## Activity Roles\n"
                    "Earn these automatically by being active in chat.\n"
                    f"- <@&{constants.active_role_id}>: marks you as an active community member.\n"
                    f"- <@&{constants.active_plus_role_id}>: Shows consistent participation in discussions.\n"
                    f"- <@&{constants.chronically_online_role_id}>: Your message count has breached the event horizon.\n"
                    f"- <@&{constants.needs_to_touch_grass_role_id}>: The final hidden evolution. Please step outside.\n\n"

                    "## Community Progression Roles\n"
                    "Awarded through community nominations (`/nominate`).\n"
                    f"- <@&{constants.boot_role_id}> 👦🏻: **Boot** - Requires <@&{constants.active_role_id}>. Nominated by 2 Apprentices, 1 Fellow, or 1 Mod.\n"
                    f"- <@&{constants.apprentice_role_id}> 👨‍💻: **Apprentice** - Requires Boot. Nominated by 2 Fellows or 1 Mod.\n"
                    f"- <@&{constants.fellow_role_id}> 👨🏻‍🎓: **Fellow** - Requires Apprentice. Nominated by 2 Mods.\n\n"

                    "## Special Recognition Roles\n"
                    "Awarded directly by staff for notable achievements.\n"
                    f"- <@&{constants.wizard_role_id}> ⚡︎: Awarded to the member currently #1 on the challenges leaderboard.\n"
                    f"- <@&{constants.trusted_role_id}> ✔: Given to long-standing members demonstrating reliability.\n"
                    f"- <@&{constants.contributor_role_id}> 🛠️: Recognizes contributions to our GitHub repositories."
                ),
                color=constants.default_color,
            )

            await interaction.response.send_message(
                embed=roles_embed,
                ephemeral=True,
            )

        elif custom_id == "Channels":
            channels_embed = discord.Embed(
                description=(
                    "- 🌟 **Getting Started**\n"
                    f"  - **<#{constants.introduction_channel_id}>**: Introduce yourself here, so like minded people can find you.\n"
                    f"  - **<#{constants.join_a_team_channel_id}>**: Join a DSA team, view all teams using the channel utility.\n\n"
                    "- 💭 **General**\n"
                    f"  - **<#{constants.general_channel_id}>**: Casual conversations and main community chat.\n"
                    f"  - **<#{constants.today_i_did_channel_id}>**: Log your daily progress or anything productive you did that day.\n"
                    f"  - **<#{constants.resume_roasting_channel}>**: Post your resume here and get roasted!\n"
                    f"  - **<#{constants.project_showcase_channel_id}>**: Showcase your project here that you are really proud of.\n"
                    f"  - **<#{constants.bot_cmd_channel_id}>**: Use all bots and bot commands here, both Tortoise and Runtime.\n"
                    f"  - **<#{constants.focus_music_channel_id}>**: Listen to lo-fi beats and focus music 24/7 365.\n\n"
                    "- ❓ **Help & discussion**\n"
                    f"  - **<#{constants.leetcode_channel_id}>**: All discussions related to Leetcode/DS-Algo and Competitive Programming.\n"
                    f"  - **<#{constants.programming_channel_id}>**: General programming discussions, All languages welcome.\n"
                    f"  - **<#{constants.opensource_channel_id}>**: Discussions about opensource projects, Homelab and Self-hosting.\n"
                    f"  - **<#{constants.discussion_voice_channel_id}>**: Common voice channel for all topics.\n\n"
                    "- 👨‍💻 **Leetcode challenges**\n"
                    f"  - **<#{constants.challenges_channel_id}>**: Weekly Leetcode Challenge will be posted here!\n"
                    f"  - **<#{constants.challenge_log_channel_id}>**: Challenge auto-submission logs will be visible here.\n"
                    f"  - **<#{constants.challenge_submission_channel_id}>**: Post your challenge solutions here so others can refer it.\n"
                    f"  - **<#{constants.challenge_discussion_channel_id}>**: Discussion related to current or previous challenges.\n\n"
                    "- 🎓 **DSA Prep Team - Tortoise**\n"
                    f"  - **<#{constants.team_plan_channel_id}>**: Weekly team plan will be posted with list of problems for you to solve daily.\n"
                    f"  - **<#{constants.team_forum_channel_id}>**: Post your daily solutions in the designated forum channels.\n"
                    f"  - **<#{constants.team_chat_channel_id}>**: All casual conversation with team members happens here.\n"
                    f"  - **<#{constants.team_discussion_channel_id}>**: Discussions related to daily problems, approaches and patterns.\n"
                    f"  - **<#{constants.team_voice_channel_id}>**: Common voice channel for all team activity.\n\n"
                    "- 💳 **Developer Marketplace**\n"
                    f"  - **<#{constants.job_board_channel_id}>**: Use this channel for posting job/paid work requests.\n"
                    f"  - **<#{constants.dev_board_channel_id}>**: Use this channel if you are a developer / service provider who want's to advertise their service. \n\n"
                ),
                color=constants.default_color,
            )
            channels_embed.set_footer(icon_url=constants.bot_avatar_url, text="Enable “Show All Channels” in server settings if some channels are not visible.")

            await interaction.response.send_message(
                embed=channels_embed,
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if (
                isinstance(message.channel, discord.TextChannel)
                and message.channel.is_news()
        ):
            try:
                await message.publish()
            except Exception:
                pass

        for match in self.message_link_pattern.finditer(message.content):
            try:
                channel = self.bot.get_channel(int(match.group(2)))
                if not channel:
                    continue

                ref = await channel.fetch_message(int(match.group(3)))

                embed = discord.Embed(
                    description=ref.content or "*[No content]*",
                    timestamp=ref.created_at,
                    color=constants.default_color,
                )
                embed.set_author(
                    name=ref.author.display_name,
                    icon_url=ref.author.display_avatar.url,
                )

                image = next(
                    (a for a in ref.attachments if is_image_attachment(a)),
                    None,
                )
                if image:
                    embed.set_image(url=image.url)

                view = View()
                view.add_item(MessageLinkButton(match.group(0)))

                await message.reply(embed=embed, view=view, mention_author=False)
            except Exception as e:
                logger.error(f"Error processing message link: {e}")

        if message.channel.id == self.target_channel_id:
            if any(is_image_attachment(a) for a in message.attachments):
                for r in self.reactions:
                    await message.add_reaction(r)

                thread = await message.create_thread(
                    name=(
                        f"Discussion: {message.content[:30]}..."
                        if message.content
                        else "Discussion"
                    )
                )
                await thread.send(
                    f"Thread created for discussing this picture by {message.author.mention}."
                )
            else:
                try:
                    await message.delete()
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_ready(self):
        await self.remove_new_member_role.start()


async def setup(bot):
    await bot.add_cog(TortoiseServer(bot))

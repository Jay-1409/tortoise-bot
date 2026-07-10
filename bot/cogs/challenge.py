import io
import json
import logging
import time
from typing import Any, Optional

import discord
from decouple import config
from discord import app_commands
from discord.ext import commands

from bot.constants import (
    challenge_boilerplate_max_bytes,
    challenge_discussion_channel_id,
    challenge_default_max_tests,
    challenge_default_points,
    challenge_execution_api_default_timeout_ms,
    challenge_execution_api_default_url,
    challenge_logs_channel_id,
    challenge_logs_channel_name,
    challenge_modal_submission_max_length,
    challenge_moderator_role_ids,
    challenge_pipeline_smoke_test_cases,
    challenge_pipeline_smoke_tests,
    challenge_problem_title_max_length,
    challenge_problem_title_min_length,
    challenge_statement_max_bytes,
    challenge_supported_languages,
    challenge_test_reveal_cost,
    challenge_tests_max_bytes,
    challenge_autocomplete_choice_max_length,
    challenge_log_channel_id,
)
from bot.utils.checks import check_if_tortoise_staff
from bot.utils.challenge import (
    ExecutionApiClient,
    Problem,
    TestCase,
    clean_slug,
    download_text,
    judge_submission,
    parse_jsonish,
    parse_test_files,
    positive_integer_env,
    slug_from_title,
)
from bot.utils.embed_handler import failure, info, success, warning


logger = logging.getLogger(__name__)

LANGUAGE_CHOICES = [
    app_commands.Choice(name=name, value=value)
    for name, value in challenge_supported_languages
]

PIPELINE_LANGUAGE_CHOICES = [
    app_commands.Choice(name="All languages", value="all"),
    *LANGUAGE_CHOICES,
]


async def challenge_problem_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    cog = interaction.client.get_cog("Challenge")
    if cog is None:
        return []
    return await cog.autocomplete_problem(interaction, current)


def is_challenge_moderator_member(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id:
        return True

    return any(role.id in challenge_moderator_role_ids for role in member.roles)


class SolutionSubmissionModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        cog: "Challenge",
        problem_slug: str,
        language_value: str,
        language_name: str,
    ):
        super().__init__(title="Submit solution")
        self.cog = cog
        self.problem_slug = problem_slug
        self.language_value = language_value
        self.language_name = language_name
        self.solution = discord.ui.TextInput(
            label="Function implementation",
            style=discord.TextStyle.paragraph,
            placeholder="Paste only the function implementation here.",
            required=True,
            max_length=challenge_modal_submission_max_length,
        )
        self.add_item(self.solution)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.process_submission(
            interaction=interaction,
            problem_slug=self.problem_slug,
            language_value=self.language_value,
            language_name=self.language_name,
            submitted_code=str(self.solution.value),
        )


class RevealTestsConfirmView(discord.ui.View):
    def __init__(self, *, cog: "Challenge", user_id: int, problem: Problem):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.problem = problem
        self.confirmation_step = 1

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True

        await interaction.response.send_message(
            embed=failure("Only the user who requested the reveal can confirm it."),
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Reveal test cases", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.confirmation_step == 1:
            self.confirmation_step = 2
            button.label = "Yes, reveal and deduct points"
            button.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(
                embed=warning(
                    "Second confirmation required.\n\n"
                    f"Revealing these hidden test cases costs **{challenge_test_reveal_cost} points** the first time "
                    "you reveal this problem. Press the red button again to continue."
                ),
                view=self,
            )
            return

        self.disable_all_buttons()
        await interaction.response.edit_message(view=self)
        await self.cog.reveal_tests_for_user(interaction, self.problem)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.disable_all_buttons()
        await interaction.response.edit_message(embed=warning("Test case reveal cancelled."), view=self)


class Challenge(commands.Cog):
    """Automated coding challenges powered by Hermes Engine."""

    challenge_group = app_commands.Group(
        name="challenge",
        description="Coding challenge commands.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.max_tests = challenge_default_max_tests
        self.hermes = ExecutionApiClient(
            url=config("EXECUTION_API_URL", default=challenge_execution_api_default_url),
            api_token=config("EXECUTION_API_KEY", default=None),
            timeout_seconds=positive_integer_env(
                "EXECUTION_API_TIMEOUT_MS",
                challenge_execution_api_default_timeout_ms,
            ) / 1000,
        )

    async def cog_load(self):
        await self.setup_tables()

    async def setup_tables(self):
        await self.bot.db.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS challenge_problems (
                guild_id BIGINT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                statement TEXT NOT NULL DEFAULT '',
                points INTEGER NOT NULL CHECK(points > 0),
                boilerplates JSONB NOT NULL,
                tests JSONB NOT NULL,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY (guild_id, slug)
            )
            """
        )
        await self.bot.db.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS challenge_submissions (
                id BIGSERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                problem_slug TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                language TEXT NOT NULL,
                status TEXT NOT NULL,
                passed_tests INTEGER NOT NULL DEFAULT 0,
                total_tests INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await self.bot.db.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS challenge_solves (
                guild_id BIGINT NOT NULL,
                problem_slug TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                points INTEGER NOT NULL,
                solved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (guild_id, problem_slug, user_id)
            )
            """
        )
        await self.bot.db.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS challenge_solves_leaderboard
            ON challenge_solves(guild_id, user_id)
            """
        )

    @staticmethod
    def build_rules_embed(user: Any):
        return info(
            (
                "Participants who submit a valid working solution will be awarded points "
                "and featured on the leaderboard.\n\n"
                "**Guidelines:**\n"
                "- Start with a brute force approach if needed, then optimize for time and space complexity.\n"
                "- Do not use AI assistance.\n"
                f"- Discussions are allowed in <#{challenge_discussion_channel_id}>, but do not share full solutions.\n"
                "- Any programming language is allowed.\n\n"
                "**Complexity Target:**\n"
                "- Aim for O(N) time and O(N) space or the best achievable complexity.\n"
                f"- All valid submissions receive {challenge_default_points} points.\n\n"
                "**Submission Rules:**\n"
                f"- Use `/challenge submit` to submit your solution.\n"
                f"- Challenge discussion belongs in <#{challenge_discussion_channel_id}>."
            ),
            user,
            "Challenge Guidelines",
        )

    async def send_points_log(self, embed: discord.Embed):
        channel = self.bot.get_channel(challenge_log_channel_id)
        if channel is None:
            logger.warning("Points log channel not found: %s", challenge_log_channel_id)
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("Failed to send challenge points log")

    @challenge_group.command(name="rules", description="Show challenge guidelines.")
    async def challenge_rules(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.build_rules_embed(self.bot.user))

    @challenge_group.command(name="add-points", description="Give points to a user.")
    @app_commands.check(check_if_tortoise_staff)
    @app_commands.describe(
        member="Member receiving points.",
        amount="Number of points to add.",
        reason="Optional reason shown in the log and DM.",
        silent="Whether to skip DMing the member.",
    )
    async def challenge_add_points(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 10_000],
        reason: Optional[str] = None,
        silent: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)

        new_total = await self.bot.points_manager.add_points(interaction.guild.id, member.id, amount)
        desc = (
            f"{member.mention} received **{amount}** points.\n"
            f"New total: **{new_total}** points."
        )
        dm_desc = (
            f"You were awarded **{amount}** points.\n"
            f"New total: **{new_total}** points."
        )
        if reason:
            desc += f"\n\n**Reason:** {reason}"
            dm_desc += f"\n\n**Comment:** {reason}"

        await self.send_points_log(
            info(desc, self.bot.user, "Points Awarded", f"Given by {interaction.user.display_name}")
        )

        if not silent:
            try:
                await member.send(embed=info(dm_desc, self.bot.user, "Congratulations 🌟"))
            except discord.Forbidden:
                pass

        await interaction.followup.send(
            embed=success(f"{amount} points awarded. New total: {new_total}"),
            ephemeral=True,
        )

    @challenge_group.command(name="remove-points", description="Remove points from a user.")
    @app_commands.check(check_if_tortoise_staff)
    @app_commands.describe(
        member="Member losing points.",
        amount="Number of points to remove.",
        silent="Whether to skip DMing the member.",
    )
    async def challenge_remove_points(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 10_000],
        silent: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)

        new_total = await self.bot.points_manager.remove_points(interaction.guild.id, member.id, amount)
        await self.send_points_log(
            info(
                (
                    f"**{amount}** points removed from {member.mention}\n"
                    f"New total: **{new_total}** points."
                ),
                self.bot.user,
                "Points Removed",
                f"Removed by: {interaction.user.display_name}",
            )
        )

        if not silent:
            try:
                await member.send(
                    embed=info(
                        (
                            f"**{amount}** points removed\n"
                            f"New total: **{new_total}** points."
                        ),
                        self.bot.user,
                        "Points Removed ;(",
                    )
                )
            except discord.Forbidden:
                pass

        await interaction.followup.send(
            embed=success(f"{amount} points removed. New total: {new_total}"),
            ephemeral=True,
        )

    @challenge_group.command(name="leaderboard", description="Show the points leaderboard.")
    async def challenge_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()

        entries = await self.bot.points_manager.get_leaderboard(
            interaction.guild.id,
            min_points=1,
            limit=10,
        )
        if not entries:
            await interaction.followup.send(embed=warning("No one has any points yet."), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            color=discord.Color.gold(),
        )
        medals = ["🥇", "🥈", "🥉"]
        for idx, (user_id, points) in enumerate(entries, start=1):
            member = interaction.guild.get_member(user_id)
            name = member.mention if member else f"<@{user_id}>"
            rank = medals[idx - 1] if idx <= 3 else f"#{idx}"
            embed.add_field(
                name=f"**{points}** points",
                value=f"{rank} {name}",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @challenge_group.command(name="points", description="Check points.")
    @app_commands.describe(member="Member to check. Defaults to you.")
    async def challenge_points(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ):
        target = member or interaction.user
        pts = await self.bot.points_manager.get_points(interaction.guild.id, target.id)
        await interaction.response.send_message(
            embed=info(f"{target.mention} has **{pts}** points.", self.bot.user, "Points"),
            ephemeral=True,
        )

    @challenge_group.command(name="add", description="Create or update a coding problem.")
    @app_commands.check(check_if_tortoise_staff)
    @app_commands.describe(
        title="Problem title.",
        statement="Markdown/text file containing the full problem statement.",
        python_boilerplate="Python driver/starter file containing {{SOLUTION}}.",
        javascript_boilerplate="JavaScript driver/starter file containing {{SOLUTION}}.",
        cpp_boilerplate="C++ driver/starter file containing {{SOLUTION}}.",
        java_boilerplate="Java driver/starter file containing {{SOLUTION}}.",
        test_inputs="JSON array of private input strings.",
        expected_outputs="JSON array of expected output strings.",
    )
    async def challenge_add(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, challenge_problem_title_min_length, challenge_problem_title_max_length],
        statement: discord.Attachment,
        python_boilerplate: discord.Attachment,
        javascript_boilerplate: discord.Attachment,
        cpp_boilerplate: discord.Attachment,
        java_boilerplate: discord.Attachment,
        test_inputs: discord.Attachment,
        expected_outputs: discord.Attachment,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            problem_statement = await download_text(statement, max_bytes=challenge_statement_max_bytes)
            if not problem_statement.strip():
                raise ValueError("problem statement cannot be empty.")

            boilerplates = {
                "python": await download_text(python_boilerplate, max_bytes=challenge_boilerplate_max_bytes),
                "javascript": await download_text(javascript_boilerplate, max_bytes=challenge_boilerplate_max_bytes),
                "cpp": await download_text(cpp_boilerplate, max_bytes=challenge_boilerplate_max_bytes),
                "java": await download_text(java_boilerplate, max_bytes=challenge_boilerplate_max_bytes),
            }
            for language, boilerplate in boilerplates.items():
                if "{{SOLUTION}}" not in boilerplate:
                    raise ValueError(f"{language} boilerplate must contain a {{SOLUTION}} marker.")

            inputs_text = await download_text(test_inputs, max_bytes=challenge_tests_max_bytes)
            outputs_text = await download_text(expected_outputs, max_bytes=challenge_tests_max_bytes)
            tests = parse_test_files(inputs_text, outputs_text, self.max_tests)

            slug = slug_from_title(str(title))
            await self.upsert_problem(
                guild_id=interaction.guild_id,
                slug=slug,
                title=str(title),
                statement=problem_statement,
                boilerplates=boilerplates,
                tests=tests,
                created_by=interaction.user.id,
            )
        except Exception as exc:
            logger.exception("Could not add challenge problem")
            await interaction.followup.send(embed=failure(f"Could not save problem: {exc}"), ephemeral=True)
            return

        await interaction.followup.send(
            embed=success(
                f"Saved **{title}** for Python, JavaScript, C++, and Java "
                f"with **{len(tests)}** hidden test(s). A full pass awards **{challenge_default_points} points**."
            ),
            ephemeral=True,
        )

    @challenge_group.command(name="remove", description="Deactivate a coding problem.")
    @app_commands.check(check_if_tortoise_staff)
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.describe(problem="Problem title.")
    async def challenge_remove(self, interaction: discord.Interaction, problem: str):
        await interaction.response.defer(ephemeral=True)

        removed = await self.deactivate_problem(interaction.guild_id, clean_slug(problem))
        if not removed:
            await interaction.followup.send(embed=failure("Problem not found."), ephemeral=True)
            return

        await interaction.followup.send(embed=success(f"Removed problem `{problem}`."), ephemeral=True)

    @commands.command(name="sync")
    @commands.guild_only()
    async def sync_prefix(self, ctx: commands.Context):
        if not isinstance(ctx.author, discord.Member) or not is_challenge_moderator_member(ctx.author):
            await ctx.reply(
                embed=failure("You need a moderator/admin/staff role or moderation permissions to sync commands."),
                mention_author=False,
            )
            return

        synced = await self.bot.tree.sync()
        command_names = ", ".join(f"`/{command.name}`" for command in synced) or "none"
        await ctx.reply(
            embed=success(
                f"Globally synced **{len(synced)}** application command(s): {command_names}"
            ),
            mention_author=False,
        )

    @challenge_group.command(name="view", description="List problems or download a selected problem starter.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.choices(language=LANGUAGE_CHOICES)
    @app_commands.describe(
        problem="Problem title. Leave empty to list active problems.",
        language="Language starter file to download when viewing one problem.",
    )
    async def challenge_view(
        self,
        interaction: discord.Interaction,
        problem: Optional[str] = None,
        language: Optional[app_commands.Choice[str]] = None,
    ):
        if problem is None:
            rows = await self.list_problems(interaction.guild_id)
            if not rows:
                await interaction.response.send_message(embed=warning("No active problems yet."), ephemeral=True)
                return

            body = "\n".join(
                f"`{row['slug']}` — **{row['title']}** ({row['points']} pts)"
                for row in rows
            )
            await interaction.response.send_message(embed=info(body, self.bot.user, "Active Problems"), ephemeral=True)
            return

        if language is None:
            await interaction.response.send_message(
                embed=failure("Please choose a language when viewing a specific problem."),
                ephemeral=True,
            )
            return

        selected = await self.get_problem(interaction.guild_id, clean_slug(problem))
        if selected is None:
            await interaction.response.send_message(embed=failure("Problem not found."), ephemeral=True)
            return

        boilerplate = selected.boilerplates.get(language.value)
        if boilerplate is None:
            await interaction.response.send_message(
                embed=failure(f"No {language.name} boilerplate is configured for this problem."),
                ephemeral=True,
            )
            return

        statement_file = discord.File(
            io.BytesIO(selected.statement.encode("utf-8")),
            filename=f"{selected.slug}-statement.md",
        )
        starter_file = discord.File(
            io.BytesIO(boilerplate.encode("utf-8")),
            filename=f"{selected.slug}-{language.value}-starter.txt",
        )
        preview = selected.statement.strip()
        if len(preview) > 3800:
            preview = f"{preview[:3800].rstrip()}\n\n… full statement attached."
        embed = discord.Embed(
            title=selected.title,
            description=preview or "Problem statement attached.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Points", value=str(selected.points), inline=True)
        embed.add_field(name="Language", value=language.name, inline=True)
        embed.set_footer(text="Download the starter file, implement the requested function, then use /challenge submit.")
        await interaction.response.send_message(
            embed=embed,
            files=[statement_file, starter_file],
            ephemeral=True,
        )

    @challenge_group.command(name="submit", description="Submit a solution by pasting code into a popup form.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.choices(language=LANGUAGE_CHOICES)
    @app_commands.describe(
        problem="Problem title.",
        language="Language of your submitted function.",
    )
    async def challenge_submit(
        self,
        interaction: discord.Interaction,
        problem: str,
        language: app_commands.Choice[str],
    ):
        await interaction.response.send_modal(
            SolutionSubmissionModal(
                cog=self,
                problem_slug=clean_slug(problem),
                language_value=language.value,
                language_name=language.name,
            )
        )

    @challenge_group.command(
        name="test-pipeline",
        description="Run a mod-only smoke test of the challenge submission pipeline.",
    )
    @app_commands.check(check_if_tortoise_staff)
    @app_commands.choices(language=PIPELINE_LANGUAGE_CHOICES)
    @app_commands.describe(language="Language to test. Leave blank or choose All languages for full coverage.")
    async def challenge_test_pipeline(
        self,
        interaction: discord.Interaction,
        language: Optional[app_commands.Choice[str]] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        tests = [
            TestCase(name=name, input=test_input, expected=expected)
            for name, test_input, expected in challenge_pipeline_smoke_test_cases
        ]
        language_values = (
            list(challenge_pipeline_smoke_tests.keys())
            if language is None or language.value == "all"
            else [language.value]
        )

        results = []
        total_started_at = time.perf_counter()
        for language_value in language_values:
            sample = challenge_pipeline_smoke_tests[language_value]
            started_at = time.perf_counter()
            result = await judge_submission(
                hermes=self.hermes,
                language=language_value,
                solution=sample["solution"],
                boilerplate=sample["boilerplate"],
                tests=tests,
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            results.append((language_value, sample, result, elapsed_ms))

        total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000)
        all_accepted = all(result.accepted for _, _, result, _ in results)
        status_lines = []
        diagnostics = []

        for _, sample, result, elapsed_ms in results:
            icon = "✅" if result.accepted else "❌"
            status = "passed" if result.accepted else f"failed on {result.failed_test}: {result.error}"
            status_lines.append(
                f"{icon} **{sample['name']}** — {status} "
                f"({result.passed}/{result.total}, {elapsed_ms} ms)"
            )
            if result.diagnostic:
                diagnostics.append(f"{sample['name']}: {result.diagnostic[:700]}")

        test_details = "\n".join(
            f"- {test.name}: input `{test.input.strip()}` → expected `{test.expected}`"
            for test in tests
        )
        summary = (
            "This smoke test injects a tiny `add(a, b)` implementation into each language's "
            "`{{SOLUTION}}` boilerplate, feeds stdin through the same judge wrapper, sends it to "
            "Hermes, compares stdout, and does not write submissions/solves or change points."
        )
        description = (
            f"{summary}\n\n"
            f"**Hermes endpoint:** `{self.hermes.url}`\n"
            f"**Tests:**\n{test_details}\n\n"
            f"**Results:**\n" + "\n".join(status_lines) + f"\n\nTotal time: **{total_elapsed_ms} ms**"
        )
        if diagnostics:
            description += "\n\n**Diagnostics:**\n```text\n" + "\n\n".join(diagnostics)[:1400] + "\n```"

        embed_factory = success if all_accepted else failure
        await interaction.followup.send(
            embed=embed_factory(
                description,
            ),
            ephemeral=True,
        )

    @challenge_group.command(name="reveal-tests", description="Reveal hidden test cases for a problem for points.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.describe(problem="Problem title.")
    async def challenge_reveal_tests(self, interaction: discord.Interaction, problem: str):
        selected = await self.get_problem(interaction.guild_id, clean_slug(problem))
        if selected is None:
            await interaction.response.send_message(embed=failure("Problem not found."), ephemeral=True)
            return

        already_revealed = await self.has_revealed_tests(
            guild_id=interaction.guild_id,
            slug=selected.slug,
            user_id=interaction.user.id,
        )
        cost_message = (
            "You have already revealed this problem before, so revealing it again costs **0 points**."
            if already_revealed
            else f"This will deduct **{challenge_test_reveal_cost} points** from your score."
        )

        await interaction.response.send_message(
            embed=warning(
                f"You are about to reveal all hidden test cases for **{selected.title}**.\n\n"
                f"{cost_message}\n\n"
                "This can spoil the challenge. Confirm twice to continue."
            ),
            view=RevealTestsConfirmView(cog=self, user_id=interaction.user.id, problem=selected),
            ephemeral=True,
        )

    async def process_submission(
        self,
        *,
        interaction: discord.Interaction,
        problem_slug: str,
        language_value: str,
        language_name: str,
        submitted_code: str,
    ):
        selected = await self.get_problem(interaction.guild_id, problem_slug)
        if selected is None:
            await interaction.followup.send(embed=failure("Problem not found."), ephemeral=True)
            return

        boilerplate = selected.boilerplates.get(language_value)
        if boilerplate is None:
            await interaction.followup.send(
                embed=failure(f"No {language_name} boilerplate is configured for this problem."),
                ephemeral=True,
            )
            return

        judge_started_at = time.perf_counter()
        try:
            result = await judge_submission(
                hermes=self.hermes,
                language=language_value,
                solution=submitted_code,
                boilerplate=boilerplate,
                tests=selected.tests,
            )
        except Exception as exc:
            logger.exception("Judge failed before recording submission")
            await interaction.followup.send(embed=failure(f"Judge unavailable: {exc}"), ephemeral=True)
            return
        judge_elapsed_ms = round((time.perf_counter() - judge_started_at) * 1000)

        if result.diagnostic:
            logger.warning(
                "Submission diagnostic guild=%s slug=%s user=%s: %s",
                interaction.guild_id,
                selected.slug,
                interaction.user.id,
                result.diagnostic,
            )

        await self.record_submission(
            guild_id=interaction.guild_id,
            slug=selected.slug,
            user_id=interaction.user.id,
            language=language_value,
            status="accepted" if result.accepted else "rejected",
            passed=result.passed,
            total=result.total,
            error=result.error,
        )

        if not result.accepted:
            await interaction.followup.send(
                embed=failure(
                    f"{result.error} on **{result.failed_test}** "
                    f"({result.passed}/{result.total} passed)."
                ),
                ephemeral=True,
            )
            return

        previous_rank = await self.get_points_rank(interaction.guild_id, interaction.user.id)
        newly_solved = await self.award_solve(
            guild_id=interaction.guild_id,
            slug=selected.slug,
            user_id=interaction.user.id,
            points=selected.points,
        )

        if newly_solved:
            total_points = await self.bot.points_manager.add_points(
                interaction.guild_id,
                interaction.user.id,
                selected.points,
            )
            current_rank = await self.get_points_rank(interaction.guild_id, interaction.user.id)
            await self.log_accepted_submission(
                interaction=interaction,
                problem=selected,
                language_name=language_name,
                passed=result.passed,
                total=result.total,
                judge_elapsed_ms=judge_elapsed_ms,
                points_awarded=selected.points,
                previous_rank=previous_rank,
                current_rank=current_rank,
                total_points=total_points,
                newly_solved=True,
            )
            await interaction.followup.send(
                embed=success(
                    f"Accepted — {result.total}/{result.total} passed. "
                    f"You earned **{selected.points} points**! New total: **{total_points}**."
                ),
                ephemeral=True,
            )
        else:
            total_points = await self.bot.points_manager.get_points(
                interaction.guild_id,
                interaction.user.id,
            )
            current_rank = await self.get_points_rank(interaction.guild_id, interaction.user.id)
            await self.log_accepted_submission(
                interaction=interaction,
                problem=selected,
                language_name=language_name,
                passed=result.passed,
                total=result.total,
                judge_elapsed_ms=judge_elapsed_ms,
                points_awarded=0,
                previous_rank=previous_rank,
                current_rank=current_rank,
                total_points=total_points,
                newly_solved=False,
            )
            await interaction.followup.send(
                embed=success(
                    f"Accepted — {result.total}/{result.total} passed. "
                    "You had already solved this problem, so no duplicate points were added."
                ),
                ephemeral=True,
            )

    async def autocomplete_problem(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []

        query = current.lower()
        rows = await self.list_problems(interaction.guild_id)
        choices = []
        seen = set()

        for row in rows:
            if query and query not in row["title"].lower() and query not in row["slug"]:
                continue
            if row["slug"] in seen:
                continue
            seen.add(row["slug"])
            choices.append(app_commands.Choice(name=row["title"][:challenge_autocomplete_choice_max_length], value=row["slug"]))
            if len(choices) >= 25:
                break

        return choices

    async def upsert_problem(
        self,
        *,
        guild_id: int,
        slug: str,
        title: str,
        statement: str,
        boilerplates: dict[str, str],
        tests: list[TestCase],
        created_by: int,
    ):
        await self.bot.db.pool.execute(
            """
            INSERT INTO challenge_problems
                (guild_id, slug, title, statement, points, boilerplates, tests, created_by)
            VALUES ($1, $2, $3, $4, $8, $5::jsonb, $6::jsonb, $7)
            ON CONFLICT (guild_id, slug)
            DO UPDATE SET
                title = EXCLUDED.title,
                statement = EXCLUDED.statement,
                points = EXCLUDED.points,
                boilerplates = EXCLUDED.boilerplates,
                tests = EXCLUDED.tests,
                created_by = EXCLUDED.created_by,
                created_at = NOW(),
                active = TRUE
            """,
            guild_id,
            slug,
            title,
            statement,
            json.dumps(boilerplates),
            json.dumps([
                {"name": test.name, "input": test.input, "expected": test.expected}
                for test in tests
            ]),
            created_by,
            challenge_default_points,
        )

    async def get_problem(self, guild_id: int, slug: str) -> Optional[Problem]:
        row = await self.bot.db.pool.fetchrow(
            """
            SELECT guild_id, slug, title, statement, points, boilerplates, tests
            FROM challenge_problems
            WHERE guild_id = $1 AND slug = $2 AND active = TRUE
            """,
            guild_id,
            slug,
        )
        if row is None:
            return None

        tests_payload = parse_jsonish(row["tests"])
        return Problem(
            guild_id=row["guild_id"],
            slug=row["slug"],
            title=row["title"],
            statement=row["statement"],
            points=row["points"],
            boilerplates=dict(parse_jsonish(row["boilerplates"])),
            tests=[
                TestCase(name=test["name"], input=test["input"], expected=test["expected"])
                for test in tests_payload
            ],
        )

    async def list_problems(self, guild_id: int) -> list[Any]:
        return await self.bot.db.pool.fetch(
            """
            SELECT slug, title, points
            FROM challenge_problems
            WHERE guild_id = $1 AND active = TRUE
            ORDER BY created_at DESC
            LIMIT 25
            """,
            guild_id,
        )

    async def deactivate_problem(self, guild_id: int, slug: str) -> bool:
        result = await self.bot.db.pool.execute(
            """
            UPDATE challenge_problems
            SET active = FALSE
            WHERE guild_id = $1
              AND slug = $2
              AND active = TRUE
            """,
            guild_id,
            slug,
        )
        return result == "UPDATE 1"

    async def record_submission(
        self,
        *,
        guild_id: int,
        slug: str,
        user_id: int,
        language: str,
        status: str,
        passed: int,
        total: int,
        error: Optional[str],
    ):
        await self.bot.db.pool.execute(
            """
            INSERT INTO challenge_submissions
                (guild_id, problem_slug, user_id, language, status, passed_tests, total_tests, error_message)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            guild_id,
            slug,
            user_id,
            language,
            status,
            passed,
            total,
            error,
        )

    async def award_solve(self, *, guild_id: int, slug: str, user_id: int, points: int) -> bool:
        result = await self.bot.db.pool.execute(
            """
            INSERT INTO challenge_solves (guild_id, problem_slug, user_id, points)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, problem_slug, user_id) DO NOTHING
            """,
            guild_id,
            slug,
            user_id,
            points,
        )
        return result == "INSERT 0 1"

    async def get_points_rank(self, guild_id: int, user_id: int) -> Optional[int]:
        return await self.bot.db.pool.fetchval(
            """
            WITH ranked AS (
                SELECT
                    user_id,
                    RANK() OVER (ORDER BY points DESC) AS rank
                FROM points
                WHERE guild_id = $1
                  AND points > 0
            )
            SELECT rank
            FROM ranked
            WHERE user_id = $2
            """,
            guild_id,
            user_id,
        )

    async def log_accepted_submission(
        self,
        *,
        interaction: discord.Interaction,
        problem: Problem,
        language_name: str,
        passed: int,
        total: int,
        judge_elapsed_ms: int,
        points_awarded: int,
        previous_rank: Optional[int],
        current_rank: Optional[int],
        total_points: int,
        newly_solved: bool,
    ):
        guild = interaction.guild
        if guild is None:
            return

        channel = discord.utils.get(guild.text_channels, name=challenge_logs_channel_name)
        if channel is None:
            channel = guild.get_channel(challenge_logs_channel_id) or self.bot.get_channel(challenge_logs_channel_id)
        if channel is None:
            logger.warning("Challenge log channel not found for guild=%s", interaction.guild_id)
            return

        previous_rank_text = f"#{previous_rank}" if previous_rank is not None else "unranked"
        current_rank_text = f"#{current_rank}" if current_rank is not None else "unranked"
        if previous_rank != current_rank:
            leaderboard_change = f"{previous_rank_text} → {current_rank_text}"
        else:
            leaderboard_change = f"unchanged ({current_rank_text})"

        embed = discord.Embed(
            title="✅ Correct submission",
            description=(
                f"{interaction.user.mention} solved **{problem.title}**."
                if newly_solved
                else f"{interaction.user.mention} submitted another accepted solution for **{problem.title}**."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Problem", value=f"{problem.title} (`{problem.slug}`)", inline=False)
        embed.add_field(name="Language", value=language_name, inline=True)
        embed.add_field(name="Tests", value=f"{passed}/{total} passed", inline=True)
        embed.add_field(name="Judge time", value=f"{judge_elapsed_ms} ms", inline=True)
        embed.add_field(name="Points awarded", value=str(points_awarded), inline=True)
        embed.add_field(name="Total points", value=str(total_points), inline=True)
        embed.add_field(name="Leaderboard", value=leaderboard_change, inline=True)

        try:
            await channel.send(content=interaction.user.mention, embed=embed)
        except discord.HTTPException:
            logger.exception(
                "Failed to send challenge accepted log guild=%s channel=%s user=%s slug=%s",
                interaction.guild_id,
                getattr(channel, "id", None),
                interaction.user.id,
                problem.slug,
            )

    async def has_revealed_tests(self, *, guild_id: int, slug: str, user_id: int) -> bool:
        return bool(
            await self.bot.db.pool.fetchval(
                """
                SELECT 1
                FROM challenge_submissions
                WHERE guild_id = $1
                  AND problem_slug = $2
                  AND user_id = $3
                  AND status = 'tests_revealed'
                """,
                guild_id,
                slug,
                user_id,
            )
        )

    async def mark_tests_revealed(self, *, guild_id: int, slug: str, user_id: int) -> bool:
        if await self.has_revealed_tests(guild_id=guild_id, slug=slug, user_id=user_id):
            return False

        await self.bot.db.pool.execute(
            """
            INSERT INTO challenge_submissions
                (guild_id, problem_slug, user_id, language, status, passed_tests, total_tests, error_message)
            VALUES ($1, $2, $3, 'reveal', 'tests_revealed', 0, 0, $4)
            """,
            guild_id,
            slug,
            user_id,
            f"Deducted {challenge_test_reveal_cost} points for revealing test cases",
        )
        return True

    async def reveal_tests_for_user(self, interaction: discord.Interaction, problem: Problem):
        should_deduct = await self.mark_tests_revealed(
            guild_id=interaction.guild_id,
            slug=problem.slug,
            user_id=interaction.user.id,
        )

        new_total: Optional[int] = None
        if should_deduct:
            new_total = await self.bot.points_manager.remove_points(
                interaction.guild_id,
                interaction.user.id,
                challenge_test_reveal_cost,
            )

        payload = {
            "problem": problem.title,
            "slug": problem.slug,
            "revealed_by": interaction.user.id,
            "points_deducted": challenge_test_reveal_cost if should_deduct else 0,
            "tests": [
                {
                    "name": test.name,
                    "input": test.input,
                    "expected": test.expected,
                }
                for test in problem.tests
            ],
        }
        file = discord.File(
            io.BytesIO(json.dumps(payload, indent=2).encode("utf-8")),
            filename=f"{problem.slug}-test-cases.json",
        )

        if should_deduct:
            message = (
                f"Revealed hidden tests for **{problem.title}**. "
                f"Deducted **{challenge_test_reveal_cost} points**. New total: **{new_total}**."
            )
        else:
            message = (
                f"Revealed hidden tests for **{problem.title}** again. "
                "No points were deducted because you already used this reveal."
            )

        await interaction.followup.send(embed=success(message), file=file, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Challenge(bot))

import io
import json
import logging
import os
import re
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

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
    app_commands.Choice(name="Python", value="python"),
    app_commands.Choice(name="JavaScript", value="javascript"),
    app_commands.Choice(name="C++", value="cpp"),
    app_commands.Choice(name="Java", value="java"),
]

PIPELINE_LANGUAGE_CHOICES = [
    app_commands.Choice(name="All languages", value="all"),
    *LANGUAGE_CHOICES,
]

PIPELINE_SMOKE_TESTS = {
    "python": {
        "name": "Python",
        "solution": "def add(a, b):\n    return a + b\n",
        "boilerplate": "{{SOLUTION}}\na, b = map(int, input().split())\nprint(add(a, b))\n",
    },
    "javascript": {
        "name": "JavaScript",
        "solution": "function add(a, b) {\n  return a + b;\n}\n",
        "boilerplate": (
            "{{SOLUTION}}\n"
            "const fs = require('fs');\n"
            "const [a, b] = fs.readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\n"
            "console.log(add(a, b));\n"
        ),
    },
    "cpp": {
        "name": "C++",
        "solution": "int add(int a, int b) {\n    return a + b;\n}\n",
        "boilerplate": (
            "#include <iostream>\n"
            "using namespace std;\n"
            "{{SOLUTION}}\n"
            "int main() {\n"
            "    int a, b;\n"
            "    cin >> a >> b;\n"
            "    cout << add(a, b);\n"
            "    return 0;\n"
            "}\n"
        ),
    },
    "java": {
        "name": "Java",
        "solution": "static int add(int a, int b) {\n    return a + b;\n}\n",
        "boilerplate": (
            "public class Main {\n"
            "    {{SOLUTION}}\n"
            "    public static void main(String[] args) throws Exception {\n"
            "        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));\n"
            "        String[] parts = br.readLine().trim().split(\"\\\\s+\");\n"
            "        System.out.print(add(Integer.parseInt(parts[0]), Integer.parseInt(parts[1])));\n"
            "    }\n"
            "}\n"
        ),
    },
}


async def challenge_problem_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    cog = interaction.client.get_cog("Challenge")
    if cog is None:
        return []
    return await cog.autocomplete_problem(interaction, current)


async def check_if_challenge_moderator(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False

    if interaction.guild.owner_id == interaction.user.id:
        return True

    interaction_permissions = getattr(interaction, "permissions", None)
    if interaction_permissions and has_challenge_moderator_permissions(interaction_permissions):
        return True

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None:
        member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
        except discord.HTTPException:
            return False

    if has_challenge_moderator_permissions(member.guild_permissions):
        return True

    if any(is_moderator_role_name(role.name) for role in member.roles):
        return True

    moderator_role_ids = env_id_set("MODERATOR_ROLE_IDS")
    if not moderator_role_ids:
        return False

    return any(role.id in moderator_role_ids for role in member.roles)


def has_challenge_moderator_permissions(permissions: discord.Permissions) -> bool:
    return (
        permissions.administrator
        or permissions.manage_guild
        or permissions.manage_messages
        or permissions.moderate_members
        or permissions.kick_members
        or permissions.ban_members
    )


def env_id_set(name: str) -> set[int]:
    return {
        int(value.strip())
        for value in os.getenv(name, "").split(",")
        if value.strip().isdigit()
    }


def is_moderator_role_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
    return (
        normalized in {"moderator", "moderators", "mod", "mods", "admin", "admins", "staff"}
        or "moderator" in normalized
    )


def is_challenge_moderator_member(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id:
        return True

    if has_challenge_moderator_permissions(member.guild_permissions):
        return True

    moderator_role_ids = env_id_set("MODERATOR_ROLE_IDS")
    for role in member.roles:
        if role.id in moderator_role_ids or is_moderator_role_name(role.name):
            return True

    return False


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
            max_length=4000,
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
                    "Revealing these hidden test cases costs **50 points** the first time "
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


async def get_member_debug(interaction: discord.Interaction) -> str:
    if interaction.guild is None:
        return "Guild: none"

    user_type = type(interaction.user).__name__
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    cache_hit = member is not None
    fetch_status = "not needed"

    if member is None:
        member = interaction.guild.get_member(interaction.user.id)
        cache_hit = member is not None

    if member is None:
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
            fetch_status = "ok"
        except discord.HTTPException as exc:
            fetch_status = f"failed HTTP {getattr(exc, 'status', '?')}"

    roles = []
    guild_permissions = "unknown"
    if member is not None:
        roles = [role.name for role in member.roles if role.name != "@everyone"]
        guild_permissions = str(member.guild_permissions.value)

    interaction_permissions = getattr(interaction, "permissions", None)
    interaction_permissions_value = interaction_permissions.value if interaction_permissions else "none"

    return (
        f"Guild ID: `{interaction.guild.id}`"
        f"\nUser object: `{user_type}`"
        f"\nMember cache hit: `{cache_hit}`"
        f"\nFetch member: `{fetch_status}`"
        f"\nInteraction permissions: `{interaction_permissions_value}`"
        f"\nGuild permissions: `{guild_permissions}`"
        f"\nRoles I can see for you: {', '.join(roles[:10]) or 'none'}"
    )


class Challenge(commands.Cog):
    """Automated coding challenges powered by Hermes Engine."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.max_tests = positive_integer_env("MAX_TESTS", 30)
        self.hermes = ExecutionApiClient(
            url=os.getenv("EXECUTION_API_URL", "http://127.0.0.1:8000/execute"),
            api_token=os.getenv("EXECUTION_API_KEY") or None,
            timeout_seconds=positive_integer_env("EXECUTION_API_TIMEOUT_MS", 15000) / 1000,
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

    @app_commands.command(name="problem-add", description="Create or update a coding problem.")
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
    async def problem_add(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 2, 100],
        statement: discord.Attachment,
        python_boilerplate: discord.Attachment,
        javascript_boilerplate: discord.Attachment,
        cpp_boilerplate: discord.Attachment,
        java_boilerplate: discord.Attachment,
        test_inputs: discord.Attachment,
        expected_outputs: discord.Attachment,
    ):
        await interaction.response.defer(ephemeral=True)

        if not await check_if_challenge_moderator(interaction):
            debug = await get_member_debug(interaction)
            role_hint = (
                f"\n\nYour user ID: `{interaction.user.id}`"
                f"\n{debug}"
            )
            await interaction.followup.send(
                embed=failure(
                    "You need a moderator/admin/staff role, Manage Server, or moderation permissions "
                    f"to add problems.{role_hint}"
                ),
                ephemeral=True,
            )
            return

        try:
            problem_statement = await download_text(statement, max_bytes=100_000)
            if not problem_statement.strip():
                raise ValueError("problem statement cannot be empty.")

            boilerplates = {
                "python": await download_text(python_boilerplate, max_bytes=100_000),
                "javascript": await download_text(javascript_boilerplate, max_bytes=100_000),
                "cpp": await download_text(cpp_boilerplate, max_bytes=100_000),
                "java": await download_text(java_boilerplate, max_bytes=100_000),
            }
            for language, boilerplate in boilerplates.items():
                if "{{SOLUTION}}" not in boilerplate:
                    raise ValueError(f"{language} boilerplate must contain a {{SOLUTION}} marker.")

            inputs_text = await download_text(test_inputs, max_bytes=500_000)
            outputs_text = await download_text(expected_outputs, max_bytes=500_000)
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
                f"with **{len(tests)}** hidden test(s). A full pass awards **100 points**."
            ),
            ephemeral=True,
        )

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

    @app_commands.command(name="problems", description="List active coding problems.")
    async def problems(self, interaction: discord.Interaction):
        rows = await self.list_problems(interaction.guild_id)
        if not rows:
            await interaction.response.send_message(embed=warning("No active problems yet."), ephemeral=True)
            return

        body = "\n".join(
            f"`{row['slug']}` — **{row['title']}** ({row['points']} pts)"
            for row in rows
        )
        await interaction.response.send_message(embed=info(body, self.bot.user, "Active Problems"), ephemeral=True)

    @app_commands.command(name="problem", description="Download a problem starter file.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.choices(language=LANGUAGE_CHOICES)
    @app_commands.describe(
        problem="Problem title.",
        language="Language starter file to download.",
    )
    async def problem(
        self,
        interaction: discord.Interaction,
        problem: str,
        language: app_commands.Choice[str],
    ):
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
        embed.set_footer(text="Download the starter file, implement the requested function, then use /submit.")
        await interaction.response.send_message(
            embed=embed,
            files=[statement_file, starter_file],
            ephemeral=True,
        )

    @app_commands.command(name="submit", description="Submit a solution for judging.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.choices(language=LANGUAGE_CHOICES)
    @app_commands.describe(
        problem="Problem title.",
        language="Language of your submitted function.",
        solution="Function implementation source file.",
    )
    async def submit(
        self,
        interaction: discord.Interaction,
        problem: str,
        language: app_commands.Choice[str],
        solution: discord.Attachment,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            submitted_code = await download_text(solution, max_bytes=100_000)
        except Exception as exc:
            await interaction.followup.send(embed=failure(f"Could not read solution file: {exc}"), ephemeral=True)
            return

        await self.process_submission(
            interaction=interaction,
            problem_slug=clean_slug(problem),
            language_value=language.value,
            language_name=language.name,
            submitted_code=submitted_code,
        )

    @app_commands.command(name="submit-code", description="Submit a solution by pasting code into a popup form.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.choices(language=LANGUAGE_CHOICES)
    @app_commands.describe(
        problem="Problem title.",
        language="Language of your submitted function.",
    )
    async def submit_code(
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

    @app_commands.command(
        name="test-challenge-pipeline",
        description="Run a mod-only smoke test of the challenge submission pipeline.",
    )
    @app_commands.choices(language=PIPELINE_LANGUAGE_CHOICES)
    @app_commands.describe(language="Language to test. Leave blank or choose All languages for full coverage.")
    async def test_challenge_pipeline(
        self,
        interaction: discord.Interaction,
        language: Optional[app_commands.Choice[str]] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not await check_if_challenge_moderator(interaction):
            await interaction.followup.send(
                embed=failure(
                    "You need a moderator/admin/staff role, Manage Server, or moderation permissions "
                    "to test the challenge pipeline."
                ),
                ephemeral=True,
            )
            return

        tests = [
            TestCase(name="Smoke Test 1", input="2 3\n", expected="5"),
            TestCase(name="Smoke Test 2", input="-10 7\n", expected="-3"),
        ]
        language_values = (
            list(PIPELINE_SMOKE_TESTS.keys())
            if language is None or language.value == "all"
            else [language.value]
        )

        results = []
        total_started_at = time.perf_counter()
        for language_value in language_values:
            sample = PIPELINE_SMOKE_TESTS[language_value]
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

    @app_commands.command(name="reveal-tests-cases", description="Reveal hidden test cases for a problem for 50 points.")
    @app_commands.autocomplete(problem=challenge_problem_autocomplete)
    @app_commands.describe(problem="Problem title.")
    async def reveal_tests_cases(self, interaction: discord.Interaction, problem: str):
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
            else "This will deduct **50 points** from your score."
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
            await interaction.followup.send(
                embed=success(
                    f"Accepted — {result.total}/{result.total} passed. "
                    f"You earned **{selected.points} points**! New total: **{total_points}**."
                ),
                ephemeral=True,
            )
        else:
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
            choices.append(app_commands.Choice(name=row["title"][:100], value=row["slug"]))
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
            VALUES ($1, $2, $3, $4, 100, $5::jsonb, $6::jsonb, $7)
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
            VALUES ($1, $2, $3, 'reveal', 'tests_revealed', 0, 0, 'Deducted 50 points for revealing test cases')
            """,
            guild_id,
            slug,
            user_id,
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
                50,
            )

        payload = {
            "problem": problem.title,
            "slug": problem.slug,
            "revealed_by": interaction.user.id,
            "points_deducted": 50 if should_deduct else 0,
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
                f"Deducted **50 points**. New total: **{new_total}**."
            )
        else:
            message = (
                f"Revealed hidden tests for **{problem.title}** again. "
                "No points were deducted because you already used this reveal."
            )

        await interaction.followup.send(embed=success(message), file=file, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Challenge(bot))

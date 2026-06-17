from __future__ import annotations

import os
import json
import asyncio
import logging
from functools import partial

import torch
import torch.nn as nn
import discord
from discord import Message, Member
from discord.ext import commands

from bot import constants
from bot.utils.embed_handler import moderation_log_embed
from bot.utils.misc import get_user_avatar

logger = logging.getLogger(__name__)


class MicroTransformer(nn.Module):
    def __init__(self, vocab_size: int, embed_size: int = 16, hidden_dim: int = 32):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_size)
        self.q = nn.Linear(embed_size, embed_size)
        self.k = nn.Linear(embed_size, embed_size)
        self.v = nn.Linear(embed_size, embed_size)
        self.ffn = nn.Sequential(
            nn.Linear(embed_size, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.embedding(x)
        Q, K, V = self.q(x), self.k(x), self.v(x)
        scores = torch.matmul(Q, K.transpose(-2, -1)) / (x.shape[-1] ** 0.5)
        attention_weights = torch.softmax(scores, dim=-1)
        attention_out = torch.matmul(attention_weights, V)
        sentence_vector = attention_out.mean(dim=1)
        logits = self.ffn(sentence_vector).squeeze(-1)
        return torch.sigmoid(logits)


class SpamFilter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._guild = None
        self._trusted = None
        self._log_channel = None

        self.max_len = 20
        self.confidence_threshold = 0.85

        self.vocab = self._load_vocab("config/vocab.json")
        self.model = self._load_model("config/weights.pth")

    @property
    def guild(self):
        if self._guild is None:
            self._guild = self.bot.get_guild(constants.tortoise_guild_id)
        return self._guild

    @property
    def trusted(self):
        if self._trusted is None:
            self._trusted = self.guild.get_role(constants.trusted_role_id) if self.guild else None
        return self._trusted

    @property
    def log_channel(self):
        if self._log_channel is None:
            self._log_channel = self.bot.get_channel(constants.bot_log_channel_id)
        return self._log_channel

    def _load_vocab(self, path: str) -> dict:
        if not os.path.exists(path):
            logger.error(f"Vocabulary file missing at {path}")
            return {"<PAD>": 0, "<UNK>": 1}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_model(self, path: str) -> MicroTransformer | None:
        if not os.path.exists(path):
            logger.error(f"Model weights missing at {path}")
            return None
        try:
            model = MicroTransformer(vocab_size=len(self.vocab))
            model.load_state_dict(torch.load(path, map_location="cpu"))
            model.eval()
            return model
        except Exception as e:
            logger.error(f"Failed to initialize neural network weights: {e}")
            return None

    def _text_to_tensor(self, text: str) -> torch.Tensor:
        tokens = [self.vocab.get(word, 1) for word in text.split()]
        tokens = tokens[:self.max_len] + [0] * (self.max_len - len(tokens))
        return torch.tensor(tokens, dtype=torch.long).unsqueeze(0)

    def _predict(self, text: str) -> float:
        if not self.model or not text.strip():
            return 0.0
        with torch.no_grad():
            tensor = self._text_to_tensor(text)
            return self.model(tensor).item()

    def is_whitelisted(self, message: Message) -> bool:
        if self.guild is None:
            return True
        if message.guild is None or message.author.bot:
            return True
        if message.guild.id != constants.tortoise_guild_id:
            return True
        if not isinstance(message.author, Member):
            return True
        if message.author.guild_permissions.administrator:
            return True
        if self.trusted and self.trusted in message.author.roles:
            return True
        return False

    async def run_spam_analysis(self, message: Message):
        if self.is_whitelisted(message) or not message.content:
            return

        loop = asyncio.get_running_loop()
        spam_probability = await loop.run_in_executor(
            None,
            partial(self._predict, message.content)
        )

        if spam_probability >= self.confidence_threshold:
            await self.log_spam_incident(message, spam_probability)

    async def log_spam_incident(self, message: Message, probability: float):
        if not self.log_channel:
            return

        truncated_content = message.content[:1024]
        embed = moderation_log_embed(
            title="Suspected Spam Flagged",
            channel=message.channel.mention,
            content=(
                f"**Spam Probability:** `{probability * 100:.2f}%` "
                f"(Threshold: `{self.confidence_threshold * 100:.0f}%`)\n"
                f"**Message Content:**\n{truncated_content}\n\n"
                f"**Jump URL:** [Click here to view message]({message.jump_url})"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(
            text=f"Author: {message.author} ({message.author.id})",
            icon_url=get_user_avatar(message.author)
        )
        await self.log_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        await self.run_spam_analysis(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        if before.content == after.content:
            return
        await self.run_spam_analysis(after)


async def setup(bot: commands.Bot):
    await bot.add_cog(SpamFilter(bot))

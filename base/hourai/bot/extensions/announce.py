import asyncio
import discord
import random
from hourai.bot import cogs
from hourai.db import models
from discord.ext import commands


class Announce(cogs.BaseCog):

    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True)
    async def announce(self, ctx):
        pass

    @announce.command(name='join')
    async def announce_join(self, ctx):
        conf = ctx.guild.config.announce
        result = self.__toggle_channel(ctx, conf.joins)
        await ctx.guild.flush_config()
        suffix = 'enabled' if result else 'disabled'
        await ctx.send(f":thumbsup: Join messages {suffix}")

    @announce.command(name='leave')
    async def announce_leave(self, ctx):
        conf = ctx.guild.config.announce
        result = self.__toggle_channel(ctx, conf.leaves)
        await ctx.guild.flush_config()
        suffix = 'enabled' if result else 'disabled'
        await ctx.send(f":thumbsup: Leave  messages {suffix}")

    @announce.command(name='ban')
    async def announce_ban(self, ctx):
        conf = ctx.guild.config.announce
        result = self.__toggle_channel(ctx, conf.bans)
        await ctx.guild.flush_config()
        suffix = 'enabled' if result else 'disabled'
        await ctx.send(f":thumbsup: Ban messages {suffix}")

    def __toggle_channel(self, ctx, config):
        if ctx.channel.id in config.channel_ids:
            config.channel_ids.remove(ctx.channel.id)
            return False
        config.channel_ids.append(ctx.channel.id)
        return True

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not member.pending:
            await self.send_announce_join(member)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.pending and not after.pending:
            await self.send_announce_join(after)

    async def send_announce_join(self, member):
        announce_config = member.guild.config.announce
        if not announce_config.HasField('joins'):
            return
        if len(announce_config.joins.messages) > 0:
            choices = list(announce_config.joins.messages)
        else:
            choices = [f'**{member.mention}** has joined the server.']
        await self.__make_announcement(member.guild, announce_config.joins,
                                       choices)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, data):
        guild = self.bot.get_guild(int(data['guild_id']))
        if guild is None:
            return
        user_id = int(data['user']['id'])
        announce_config = guild.config.announce
        if not announce_config.HasField('leaves'):
            return
        with self.bot.create_storage_session() as session:
            latest_name = session.query(models.Username) \
                .filter_by(user_id=user_id) \
                .order_by(models.Username.timestamp.desc()) \
                .first()
            if latest_name is None:
                return
            if len(announce_config.leaves.messages) > 0:
                choices = list(announce_config.leaves.messages)
            else:
                choices = [f'**{latest_name.name}** has left the server.']
            await self.__make_announcement(guild, announce_config.leaves,
                                           choices)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        announce_config = guild.config.announce
        if not announce_config.HasField('bans'):
            return
        if len(announce_config.bans.messages) > 0:
            choices = list(announce_config.bans.messages)
        else:
            choices = [f'**{user.name}** has been banned.']
        await self.__make_announcement(guild, announce_config.bans, choices)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        announce_config = member.guild.config.announce
        if not announce_config.HasField('voice'):
            return
        assert not (before.channel is None and after.channel is None)
        if before.channel == after.channel:
            return
        elif before.channel is None:
            choices = [f'**{member.display_name}** joined '
                       f'**{after.channel.name}**.']
        elif after.channel is None:
            choices = [f'**{member.display_name}** left '
                       f'**{before.channel.name}**.']
        else:
            choices = [f'**{member.display_name}** moved to '
                       f'**{after.channel.name}**'
                       f' from **{before.channel.name}**.']
        await self.__make_announcement(member.guild, announce_config.voice,
                                       choices)

    async def __make_announcement(self, guild, config, choices):
        assert len(choices) > 0
        channels = [guild.get_channel(ch_id) for ch_id in config.channel_ids]
        channels = [ch for ch in channels
                    if isinstance(ch, discord.TextChannel)]
        tasks = []
        for channel in channels:
            content = random.choice(choices)
            tasks.append(channel.send(content))
        try:
            await asyncio.gather(*tasks)
        except discord.errors.Forbidden:
            pass


def setup(bot):
    bot.add_cog(Announce(bot))

import asyncio
import discord
import logging
from . import approvers, rejectors
from .context import ValidationContext
from discord.ext import commands
from datetime import datetime, timedelta
from hourai import utils
from hourai import config as hourai_config
from hourai.bot import cogs
from hourai.utils import checks

log = logging.getLogger(__name__)

PURGE_LOOKBACK = timedelta(hours=6)
PURGE_DM = ("You have been kicked from {} due to not being verified within "
            "sufficient time.  If you feel this is in error, please contact a "
            "mod regarding this.")
BATCH_SIZE = 10
MINIMUM_GUILD_SIZE = 150

APPROVE_REACTION = '\u2705'
KICK_REACTION = '\u274C'
BAN_REACTION = '\u2620'
MODLOG_REACTIONS = (APPROVE_REACTION, KICK_REACTION, BAN_REACTION)


def load_list(name):
    return hourai_config.load_list(hourai_config.get_config(), name)


# TODO(james7132): Add per-server validation configuration.
# TODO(james7132): Add filter for pornographic or violent avatars
# Validators are applied in order from first to last. If a later validator has
# an approval reason, it overrides all previous rejection reasons.
VALIDATORS = (
    # ---------------------------------------------------------------
    # Suspicion Level Validators
    #     Validators here are mostly for suspicious characteristics.
    #     These are designed with a high-recall, low precision methdology.
    #     False positives from these are more likely.  These are low severity
    #     checks.
    # -----------------------------------------------------------------

    # New user accounts are commonly used for alts of banned users.
    rejectors.NewAccountRejector(lookback=timedelta(days=30)),
    # Low effort user bots and alt accounts tend not to set an avatar.
    rejectors.NoAvatarRejector(),
    # Deleted accounts shouldn't be able to join new servers. A user
    # joining that is seemingly deleted is suspicious.
    rejectors.DeletedAccountRejector(),

    # Filter likely user bots based on usernames.
    rejectors.StringFilterRejector(
        prefix='Likely user bot. ',
        filters=load_list('user_bot_names')),
    rejectors.StringFilterRejector(
        prefix='Likely user bot. ',
        full_match=True,
        filters=load_list('user_bot_names_fullmatch')),

    # If a user has Nitro, they probably aren't an alt or user bot.
    approvers.NitroApprover(),

    # -----------------------------------------------------------------
    # Questionable Level Validators
    #     Validators here are mostly for red flags of unruly or
    #     potentially troublesome.  These are designed with a
    #     high-recall, high-precision methdology. False positives from
    #     these are more likely to occur.
    # -----------------------------------------------------------------

    # Filter usernames and nicknames that match moderator users.
    rejectors.NameMatchRejector(
        prefix='Username matches moderator\'s. ',
        filter_func=utils.is_moderator,
        min_match_length=4),
    rejectors.NameMatchRejector(
        prefix='Username matches moderator\'s. ',
        filter_func=utils.is_moderator,
        member_selector=lambda m: m.nick,
        min_match_length=4),

    # Filter usernames and nicknames that match bot users.
    rejectors.NameMatchRejector(
        prefix='Username matches bot\'s. ',
        filter_func=lambda m: m.bot,
        min_match_length=4),
    rejectors.NameMatchRejector(
        prefix='Username matches bot\'s. ',
        filter_func=lambda m: m.bot,
        member_selector=lambda m: m.nick,
        min_match_length=4),

    # Filter offensive usernames.
    rejectors.StringFilterRejector(
        prefix='Offensive username. ',
        filters=load_list('offensive_usernames')),

    # Filter sexually inapproriate usernames.
    rejectors.StringFilterRejector(
        prefix='Sexually inapproriate username. ',
        filters=load_list('sexually_inappropriate_usernames')),

    # Filter potentially long usernames that use wide unicode characters that
    # may be disruptive or spammy to other members.
    # TODO(james7132): Reenable wide unicode character filter

    # -----------------------------------------------------------------
    # Malicious Level Validators
    #     Validators here are mostly for known offenders.
    #     These are designed with a low-recall, high precision
    #     methdology. False positives from these are far less likely to
    #     occur.
    # -----------------------------------------------------------------

    # Make sure the user is not banned on other servers.
    rejectors.BannedUserRejector(min_guild_size=150),

    # Check the username against known banned users from the current
    # server. Requires exact username match (case insensitive)
    rejectors.BannedUsernameRejector(),

    # Check if the user is distinguished (Discord Staff, Verified, Partnered,
    # etc).
    approvers.DistinguishedUserApprover(),

    # All non-override users are rejected while guilds are locked down.
    rejectors.LockdownRejector(),

    # -----------------------------------------------------------------
    # Override Level Validators
    #     Validators here are made to explictly override previous
    #     validators. These are specifically targetted at a small
    #     specific group of individiuals. False positives and negatives
    #     at this level are very unlikely if not impossible.
    # -----------------------------------------------------------------
    approvers.BotApprover(),
    approvers.BotOwnerApprover(),
)


class Validation(cogs.BaseCog):

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            if not guild.me.guild_permissions.manage_guild:
                return
            await guild.invites.refresh()

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        invite.guild.invites.add(invite)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        invite.guild.invites.remove(invite)

    async def purge_guild(self, guild, cutoff_time, dry_run=True):
        role = guild.validation_role
        if role is None or not guild.me.guild_permissions.kick_members:
            return

        def _is_kickable(member):
            is_new = member.joined_at is not None and \
                     member.joined_at >= cutoff_time
            checks = (role in member.roles,                 # Is verified
                      is_new,                               # Is too new
                      member.bot,                           # Is a bot
                      member.premium_since is not None)     # Is a booster
            return not any(checks)

        async def _kick_member(member):
            try:
                await utils.send_dm(member, PURGE_DM.format(member.guild.name))
                await member.kick(reason='Unverified in sufficient time.')
            except discord.Forbidden:
                pass
            mem = utils.pretty_print(member)
            gld = utils.pretty_print(member.guild)
            log.info(
                f'Purged {mem} from {gld} for not being verified in time.')

        count = 0
        tasks = []
        async for member in guild.fetch_members(limit=None):
            if not _is_kickable(member):
                continue
            count += 1
            if not dry_run:
                tasks.append(_kick_member(member))
            if len(tasks) > 5:
                await asyncio.gather(*tasks)
                tasks.clear()
        if len(tasks) > 0:
            await asyncio.gather(*tasks)

        return count

    @commands.command(name="setmodlog")
    @checks.is_moderator()
    @commands.guild_only()
    async def setmodlog(self, ctx, channel: discord.TextChannel = None):
        # TODO(jame7132): Update this so it's in a different cog.
        channel = channel or ctx.channel
        await ctx.guild.set_modlog_channel(channel)
        await ctx.send(":thumbsup: Set {}'s modlog to {}.".format(
            ctx.guild.name,
            channel.mention if channel is not None else 'None'))

    @commands.group()
    @commands.guild_only()
    @checks.is_moderator()
    async def validation(self, ctx):
        pass

    @validation.group(name='purge')
    @commands.bot_has_permissions(kick_members=True)
    async def validation_purge(self, ctx, lookback: utils.human_timedelta):
        """Mass kicks unverified users from the server. This isn't useful
        unless validation is enabled and a role is assigned. See
        "~help validation setup" for more information. Users newer than
        "lookback" will not be kicked.

        Example Usage:
        ~validation purge 30m
        ~validation purge 1h
        ~validation purge 1d
        """
        if ctx.guild.validation_role is None:
            await ctx.send('No role has been configured. '
                           'Please configure and propagate a role first.')
        check_time = datetime.utcnow()
        async with ctx.typing():
            count = await self.purge_guild(ctx.guild, check_time - lookback,
                                           dry_run=True)
        await ctx.send(
            f'Doing this will purge {count} users from the server.\n'
            f'**Continue? y/n**', delete_after=600)
        continue_purge = await utils.wait_for_confirmation(ctx)
        if continue_purge:
            async with ctx.typing():
                await self.purge_guild(ctx.guild, check_time - lookback,
                                       dry_run=False)
            await ctx.send(f'Purged {count} unverified users from the server.')
        else:
            await ctx.send('Purged cancelled', delete_after=60)

    @validation.group(name='lockdown')
    async def validation_lockdown(self, ctx, time: utils.human_timedelta):
        """Manually locks down the server. Forces manual verification of almost
        all users joining the server for the duration. This isn't useful unless
        validation is enabled. See "~help validation setup" for more
        information. A lockdown can be lifted via "~valdiation lockdown lift".

        Example Usage:
        ~validation lockdown 30m
        ~validation lockdown 1h
        ~validation lockdown 1d
        """
        expiration = datetime.utcnow() + time
        await ctx.guild.set_lockdown(expiration)
        await ctx.send(
            f'Lockdown enabled. Will be automatically lifted at {expiration}')

    @validation_lockdown.command(name='lift')
    async def validation_lockdown_lift(self, ctx):
        """Lifts a lockdown from the server. See "~help validation lockdown" for
        more information.

        Warning: this is currently non-persistent. If the bot is restarted
        while a lockdown is in place, it will be lifted without warning.

        Example Usage:
        ~validation lockdown lift
        """
        await ctx.guild.clear_lockdown()
        await ctx.send('Lockdown disabled.')

    @validation.command(name='verify')
    async def validation_verify(self, ctx, member: discord.Member):
        """Runs verification on a provided user.
        Must be a moderator or owner of the server to use this command.
        Validation must be enabled on a server before running this. See
        "~help validation setup" for more information.

        Example Usage:
        ~validation verify @Bob
        ~validation verify Alice
        """
        config = ctx.guild.config.validation
        if not config.enabled:
            await ctx.send('Validation has not been setup. Please see '
                           '`~help validation` for more details.')
            return

        validation_ctx = ValidationContext(ctx.bot, member, config)
        await validation_ctx.validate_member(VALIDATORS)
        await validation_ctx.send_log_message(ctx, include_invite=False)

    @validation.command(name="setup")
    async def validation_setup(self, ctx, role: discord.Role = None):
        config = ctx.guild.config.validation
        config.enabled = True
        if role is not None:
            config.role_id = role.id
        else:
            config.ClearField('role_id')
        await ctx.guild.flush_config()
        await ctx.send('Validation configuration complete! Please run '
                       '`~validation propagate` to'
                       ' complete setup.')

    @validation.command(name="disable")
    async def validation_disable(self, ctx):
        ctx.guild.config.validation = False
        await ctx.guild.flush_config()
        await ctx.send('Validation disabled. To reenable, rerun `~validation '
                       'setup`.')

    @validation.command(name="propagate")
    @commands.bot_has_permissions(manage_roles=True)
    async def validation_propagate(self, ctx):
        config = ctx.guild.config.validation
        if not config.HasField('role_id'):
            await ctx.send('No validation config was found. Please run '
                           '`~valdiation setup`')
            return

        role = ctx.guild.get_role(config.role_id)
        if role is None:
            await ctx.send("Verification role not found.")
            config.ClearField('kick_unvalidated_users_after')
            await ctx.guild.flush_config()
            return

        msg = await ctx.send('Propagating validation role...!')
        last_update = float('-inf')
        total_processed = 0
        updated = 0
        async for member in ctx.guild.fetch_members(limit=None):
            total_processed += 1
            if role not in member.roles:
                await member.add_roles(role)
                updated += 1
            if updated > last_update + 10:
                await msg.edit(
                    content=f'Propagation Ongoing ({total_processed} done)...')
                last_update = updated
        await msg.edit(content='Propagation conplete!')

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not member.pending:
            await self.on_join(member)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.pending and not after.pending:
            await self.on_join(after)

    async def on_join(self, member):
        config = member.guild.config.validation
        if config is None or not config.enabled:
            return

        ctx = ValidationContext(self.bot, member, config)
        await ctx.validate_member(VALIDATORS)
        await self.verify_member(ctx)

        msg = await ctx.send_modlog_message()
        try:
            for reaction in MODLOG_REACTIONS:
                await msg.add_reaction(reaction)
        except (AttributeError, discord.errors.Forbidden):
            pass

    async def get_message(self, payload):
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or \
           payload.member == guild.me or \
           payload.emoji.name not in MODLOG_REACTIONS:
            return None
        channel = guild.get_channel(payload.channel_id)
        logging_config = guild.config.logging
        if channel is None or logging_config.modlog_channel_id != channel.id:
            return None
        return await channel.fetch_message(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        message = await self.get_message(payload)
        guild = payload.member.guild
        if message is None or len(message.embeds) <= 0 or \
           message.author != guild.me:
            return
        embed = message.embeds[0]
        user = payload.member

        try:
            target_id = int(embed.footer.text, 16)
            target = await self.bot.get_member_async(guild, target_id)
            if target is None:
                log.info(f'Member not found: {target_id}')
                return
        except ValueError:
            return

        perms = user.guild_permissions
        action = {
            APPROVE_REACTION:
                (self.approve_member_by_reaction, perms.manage_roles),
            KICK_REACTION:
                (self.kick_member_by_reaction, perms.kick_members),
            BAN_REACTION:
                (self.ban_member_by_reaction, perms.ban_members),
        }.get(payload.emoji.name)
        if action is None:
            return
        func, perm = action
        if perm:
            await func(guild, user, target)

    async def approve_member_by_reaction(self, guild, user, target):
        ctx = ValidationContext(self.bot, target, guild.config.validation)
        try:
            await self.verify_member(ctx)
            await guild.modlog.send(
                f'{APPROVE_REACTION} **{user}** manually verified **{target}**'
                f' via reaction.')
            log.info(f'Verified user {target} manually via reaction from '
                     f'{user}')
        except discord.Forbidden:
            await guild.modlog.send(
                f'{APPROVE_REACTION} Attempted')

    async def kick_member_by_reaction(self, guild, user, target):
        try:
            await target.kick(reason=(f'Failed verification.'
                                      f' Manually kicked by {user}.'))
            await guild.modlog.send(
                f'{KICK_REACTION} **{user}** kicked **{target}**'
                f' via reaction during manual verification.')
            log.info(f'Kicked user {target} manually via reaction from {user}')
        except discord.Forbidden:
            await guild.modlog.send(
                f'{KICK_REACTION} Attempted to kick {target.mention} and '
                f'failed. Bot does not have **Kick Members** permission.')

    async def ban_member_by_reaction(self, guild, user, target):
        try:
            await target.ban(reason=(f'Failed verification.'
                                     f' Manually banned by {user}.'))
            await guild.modlog.send(
                f'{BAN_REACTION} **{user}** ban **{target}**'
                f' via reaction during manual verification.')
            log.info(f'Banned user {target} manually via reaction from {user}')
        except discord.Forbidden:
            await guild.modlog.send(
                f'{BAN_REACTION} Attempted to ban {target.mention} and failed'
                f'. Bot does not have **Ban Members** permission.')

    async def verify_member(self, ctx):
        await ctx.apply_role()
        self.bot.dispatch('verify_' + ('accept' if ctx.approved else 'reject'),
                          ctx.member)

    async def report_bans(self, ban_info):
        user = ban_info.user
        # FIXME(james7132): This will not scale to multiple processes/nodes.
        members = await asyncio.gather(
                *[self.bot.get_member_async(guild, user.id)
                  for guild in self.bot.guilds])
        guilds = [member.guild for member in members if member is not None]

        contents = None
        if ban_info.reason is None:
            contents = (f"User {user.mention} ({user.id}) has been banned "
                        f"from another server.")
        else:
            contents = (f"User {user.mention} ({user.id}) has been banned "
                        f"from another server for the following reason: "
                        f"`{ban_info.reason}`.")

        await asyncio.gather(*[guild.modlog.send(contents)
                               for guild in guilds])


def setup(bot):
    bot.add_cog(Validation(bot))

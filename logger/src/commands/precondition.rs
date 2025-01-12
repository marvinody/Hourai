use crate::prelude::*;
use super::{Context, CommandError};

pub fn require_in_guild(ctx: &Context<'_>) -> Result<GuildId> {
    ctx.message
       .guild_id
       .ok_or_else(||
           CommandError::FailedPrecondition( "Command must be run in a server.").into())
}

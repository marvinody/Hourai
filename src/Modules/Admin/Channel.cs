using Discord;
using Discord.Commands;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace Hourai {

public partial class Admin {

  [Group("channel")]
  public class Channel : HouraiModule {

    [Command("create")]
    [Permission(GuildPermission.ManageChannels, Require.Both)]
    [Remarks("Creates a public channel with a specified name. Requires ``Manage Channels`` permission.")]
    public async Task Create(string name) {
      var guild = Check.InGuild(Context.Message).Guild;
      var channel = await guild.CreateTextChannelAsync(name); 
      await Success($"{channel.Mention} created.");
    }

    [Command("delete")]
    [Permission(GuildPermission.ManageChannels, Require.Both)]
    [Remarks("Deletes all mentioned channels. Requires ``Manage Channels`` permission.")]
    public Task Delete(params IGuildChannel[] channels) {
      return CommandUtility.ForEvery(Context, channels, CommandUtility.Action(
            delegate(IGuildChannel channel) {
              return channel.DeleteAsync();
            }));
    }

    [Command("list")]
    [Remarks("Responds with a list of all text channels that the bot can see on this server.")]
    public async Task List() {
      var guild = Check.InGuild(Context.Message).Guild;
      var channels = (await guild.GetChannelsAsync()).OfType<ITextChannel>();
      await Context.Message.Respond(channels.OrderBy(c => c.Position)
          .Select(c => c.Mention).Join(", "));
    }

    [Command("permissions")]
    [Remarks("Shows the channel permissions for one user on the current channel.\nShows your permisisons if no other user is specified")]
    public async Task Permissions(IGuildUser user = null) {
      user = user ?? (Context.Message.Author as IGuildUser);
      var perms = user.GetPermissions(Check.InGuild(Context.Message));
      await Context.Message.Respond(perms.ToList()
          .Select(p => p.ToString())
          .OrderBy(s => s)
          .Join(", "));
    }

  }

}

}

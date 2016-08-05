using System;
using System.Linq;
using Discord;
using Discord.Commands;

namespace DrumBot {
    public class LogService : IService {
        public void Install(DiscordClient client) {
            client.ServerAvailable += ServerLog(client, "Discovered");
            client.ServerUnavailable += ServerLog(client, "Lost");

            client.ChannelCreated += ChannelLog("created");
            client.ChannelDestroyed += ChannelLog("removed");
            
            client.UserBanned += UserLog("was banned from");
            client.UserUnbanned += UserLog("was unbanned from");
            client.UserJoined += UserLog("joined");
            client.UserLeft += UserLog("left");

            client.MessageUpdated += delegate(object sender, MessageUpdatedEventArgs e) {
                var log = $"Message update by {e.User.ToIDString()} ";
                if (e.Channel.IsPrivate)
                    log += "in private channel.";
                else
                    log += $"in {e.Channel.Name} on {e.Server.ToIDString()}";
                Log.Info(log);
                if (e.Channel.IsPrivate)
                    return;
                Log.Info($"Messagae by { e.User.Name } on { e.Channel.Name } on { e.Server.ToIDString() } was updated.");
            };
            client.MessageDeleted += MessageLog("deleted");

            client.RoleCreated += RoleLog("created");
            client.RoleUpdated += delegate(object sender, RoleUpdatedEventArgs e) {
                Log.Info($"Role { e.After.Name } on { e.Server.ToIDString() } was updated.");
            };
            client.RoleDeleted += RoleLog("deleted");

            var commandService = client.GetService<CommandService>();
            if(commandService != null)
                commandService.CommandExecuted +=
                    delegate(object s, CommandEventArgs e) {
                        var log = $"Command {e.Command.Text} executed by {e.User.ToIDString()} ";
                        if (e.Channel.IsPrivate)
                            log += "in private channel.";
                        else
                            log += $"in {e.Channel.Name} on {e.Server.ToIDString()}";
                        Log.Info(log);
                    };

        }

        EventHandler<RoleEventArgs> RoleLog(string eventType) {
            return delegate(object sender, RoleEventArgs e) {
                Log.Info($"Role { e.Role.Name } on { e.Server.ToIDString() } was { eventType }.");
            };
        }

        EventHandler<MessageEventArgs> MessageLog(string eventType) {
            return delegate(object sender, MessageEventArgs e) {
                Log.Info($"Message on { e.Channel.Name } on { e.Server.ToIDString() } was { eventType }.");
            };
        }

        EventHandler<UserEventArgs> UserLog(string eventType) {
            return delegate (object sender, UserEventArgs e) {
                Log.Info($"User { e.User.ToIDString() } {eventType} { e.Server.ToIDString() }");
            };
        }

        EventHandler<ChannelEventArgs> ChannelLog(string eventType) {
            return delegate (object sender, ChannelEventArgs e) {
                Log.Info($"Channel {eventType}: {e.Channel.ToIDString()} on server {e.Server.ToIDString()}");
            };
        }

        EventHandler<ServerEventArgs> ServerLog(DiscordClient client, string eventType) {
            return delegate (object sender, ServerEventArgs e) {
                    Log.Info($"{eventType} server {e.Server.ToIDString()}. Server Count: { client.Servers.Count() }");
            };
        }
    }
}
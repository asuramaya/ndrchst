package com.ndrchst.auth;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.context.CommandContext;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.arguments.EntityArgument;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * `/daily` — explicit, once-per-24h tier reward. Dispenses the player's tier
 * loot table (ndrchst:daily/&lt;tier&gt;) via the vanilla `/loot` command, so the
 * rewards are pure datapack (no economy mod). `/ndrchst daily reset &lt;player&gt;`
 * is the op escape hatch.
 *
 * v1 keeps the tier + cooldown in memory (set on verified login); they reset on
 * a server restart — fine for v1, persist via SavedData later.
 */
final class DailyCommand {
    private DailyCommand() {}

    static final Map<UUID, String> TIER = new ConcurrentHashMap<>();
    private static final Map<UUID, Instant> LAST = new ConcurrentHashMap<>();
    private static final long COOLDOWN_HOURS = 24;

    static void register(CommandDispatcher<CommandSourceStack> d) {
        d.register(Commands.literal("daily").executes(DailyCommand::claim));
        d.register(Commands.literal("ndrchst")
                .requires(s -> s.hasPermission(2))
                .then(Commands.literal("daily").then(Commands.literal("reset")
                        .then(Commands.argument("player", EntityArgument.player())
                                .executes(DailyCommand::reset)))));
    }

    private static int claim(CommandContext<CommandSourceStack> ctx) {
        CommandSourceStack src = ctx.getSource();
        ServerPlayer p = src.getPlayer();
        if (p == null) {
            src.sendFailure(Component.literal("Only players can claim a daily."));
            return 0;
        }
        String tier = TIER.get(p.getUUID());
        if (tier == null) {
            src.sendFailure(Component.literal(
                    "ndrchst — hold $NDRCHST and sign in through the launcher to claim a daily."));
            return 0;
        }
        Instant last = LAST.get(p.getUUID());
        if (last != null) {
            long minsLeft = COOLDOWN_HOURS * 60 - Duration.between(last, Instant.now()).toMinutes();
            if (minsLeft > 0) {
                long h = minsLeft / 60, m = minsLeft % 60;
                src.sendSuccess(() -> Component.literal(
                        "Daily already claimed — come back in " + h + "h " + m + "m."), false);
                return 0;
            }
        }
        MinecraftServer server = p.getServer();
        if (server == null) {
            return 0;
        }
        server.getCommands().performPrefixedCommand(
                server.createCommandSourceStack().withSuppressedOutput().withPermission(4),
                "loot give " + p.getGameProfile().getName() + " loot ndrchst:daily/" + tier);
        LAST.put(p.getUUID(), Instant.now());
        src.sendSuccess(() -> Component.literal("Claimed your " + tier + " daily reward!"), false);
        return 1;
    }

    private static int reset(CommandContext<CommandSourceStack> ctx) {
        try {
            ServerPlayer target = EntityArgument.getPlayer(ctx, "player");
            LAST.remove(target.getUUID());
            ctx.getSource().sendSuccess(() -> Component.literal(
                    "Reset daily cooldown for " + target.getGameProfile().getName()), true);
            return 1;
        } catch (Exception e) {
            ctx.getSource().sendFailure(Component.literal("Player not found."));
            return 0;
        }
    }
}

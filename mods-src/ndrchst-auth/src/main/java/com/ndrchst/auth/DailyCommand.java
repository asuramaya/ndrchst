package com.ndrchst.auth;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.context.CommandContext;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * `/claim` — explicit tier reward crate on a configurable cooldown (op-tunable
 * `daily_cooldown_s`), dispensed via the vanilla `/loot` command from the
 * per-tier datapack table (ndrchst:daily/&lt;tier&gt;).
 *
 * The box is authoritative: it enforces the cooldown (durable across restarts,
 * keyed by wallet) and returns the reward tier from the hourly holdings
 * snapshot — so the cooldown survives reboots and the tier can't be refreshed
 * mid-session to farm a transfer carousel. The mod only stashes each player's
 * verified wallet at login and asks the box at claim time.
 *
 * `/ndrchst claim reset &lt;player&gt;` is the op escape hatch.
 */
final class DailyCommand {
    private DailyCommand() {}

    /** Verified wallet per player UUID, set at login (NdrchstAuth.onPlayerLogin). */
    static final Map<UUID, String> WALLET = new ConcurrentHashMap<>();

    static void register(CommandDispatcher<CommandSourceStack> d) {
        d.register(Commands.literal("claim").executes(DailyCommand::claim));
        // The op tree (`/ndrchst …`, incl. `claim reset`) lives in OpsCommand.
    }

    private static int claim(CommandContext<CommandSourceStack> ctx) {
        CommandSourceStack src = ctx.getSource();
        ServerPlayer p = src.getPlayer();
        if (p == null) {
            src.sendFailure(Component.literal("Only players can claim a daily."));
            return 0;
        }
        String wallet = WALLET.get(p.getUUID());
        if (wallet == null) {
            src.sendFailure(Component.literal(
                    "ndrchst — sign in through the launcher to claim your crate."));
            return 0;
        }
        DailyClient.ClaimResult r = DailyClient.claim(wallet);
        if (r == null) {
            src.sendFailure(Component.literal(
                    "ndrchst — couldn't reach the server, try again in a moment."));
            return 0;
        }
        if (!r.ok()) {
            long mins = r.secondsLeft() / 60;
            long h = mins / 60, m = mins % 60;
            src.sendSuccess(() -> Component.literal(
                    "Crate already claimed — back in " + h + "h " + m + "m."), false);
            return 0;
        }
        MinecraftServer server = p.getServer();
        if (server == null) {
            return 0;
        }
        String tier = r.tier();
        server.getCommands().performPrefixedCommand(
                server.createCommandSourceStack().withSuppressedOutput().withPermission(4),
                "loot give " + p.getGameProfile().getName() + " loot ndrchst:daily/" + tier);
        src.sendSuccess(() -> Component.literal("Opened your " + tier + " crate!"), false);
        return 1;
    }
}

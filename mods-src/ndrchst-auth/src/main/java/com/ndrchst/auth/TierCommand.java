package com.ndrchst.auth;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.context.CommandContext;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * {@code /tier} — a player checks their own $NDRCHST standing: tier, holdings %,
 * and how much more to reach the next rung. The wallet is the one stashed at
 * login ({@link NdrchstAuth#onPlayerLogin}); the box answers from its DB.
 *
 * <p>The lookup is a (localhost, but still blocking) HTTP call, so it runs on a
 * worker thread and the rendered result is posted back onto the server thread —
 * a flaky box can't stall a game tick.
 */
final class TierCommand {
    private TierCommand() {}

    static void register(CommandDispatcher<CommandSourceStack> d) {
        d.register(Commands.literal("tier").executes(ctx -> run(ctx, false)));
        d.register(Commands.literal("price").executes(ctx -> run(ctx, true)));
    }

    /** Shared path for /tier (full standing) and /price (just the ticker line).
     *  Both look the wallet up on the box off-thread, then render on the server
     *  thread. priceOnly trims the readout to the cached $NDRCHST ticker. */
    private static int run(CommandContext<CommandSourceStack> ctx, boolean priceOnly) {
        CommandSourceStack src = ctx.getSource();
        ServerPlayer p = src.getPlayer();
        if (p == null) {
            src.sendFailure(Component.literal("Only players can use this."));
            return 0;
        }
        String wallet = DailyCommand.WALLET.get(p.getUUID());
        if (wallet == null) {
            src.sendFailure(Component.literal(
                    "ndrchst — sign in through the launcher first."));
            return 0;
        }
        MinecraftServer server = p.getServer();
        if (server == null) {
            return 0;
        }
        UUID uuid = p.getUUID();
        CompletableFuture.runAsync(() -> {
            TierClient.Standing s = TierClient.lookup(wallet);
            server.execute(() -> {
                ServerPlayer pl = server.getPlayerList().getPlayer(uuid);
                if (pl == null) {
                    return;  // player left while we were asking the box
                }
                if (s == null) {
                    pl.sendSystemMessage(Component.literal(
                            "ndrchst — couldn't reach the server, try again in a moment.")
                            .withStyle(ChatFormatting.RED));
                    return;
                }
                if (priceOnly) {
                    Component line = Flares.priceLine(s);
                    pl.sendSystemMessage(line != null ? line : Component.literal(
                            "ndrchst — no price yet, check back shortly.")
                            .withStyle(ChatFormatting.GRAY));
                    return;
                }
                for (Component line : Flares.standing(s)) {
                    pl.sendSystemMessage(line);
                }
            });
        });
        return 1;
    }
}

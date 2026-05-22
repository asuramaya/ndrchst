package com.ndrchst.auth;

import java.util.concurrent.CompletableFuture;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.network.protocol.game.ClientboundTabListPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * Ambient $NDRCHST presence in the tab menu (hold Tab): a branded header + a
 * live price / market-cap footer. The price is the box's cached ticker
 * (DexScreener, <b>0 RPC</b>); a slow refresh pulls it off the server thread and
 * pushes the header/footer packet to everyone. Minimal — just a tab packet, no
 * scoreboard objective, no per-tick work beyond a counter.
 */
final class TabList {
    private TabList() {}

    private static final Component HEADER = Component.empty()
            .append(Component.literal("ndrchst")
                    .withStyle(ChatFormatting.AQUA, ChatFormatting.BOLD))
            .append(Component.literal("  ·  holdings are rank")
                    .withStyle(ChatFormatting.GRAY));

    // Last good cached price (volatile — written by the refresh worker, read on
    // the server thread). NaN until the first successful pull.
    private static volatile double priceUsd = Double.NaN;
    private static volatile double marketCap = Double.NaN;

    /** Pull the latest cached price from the box (off-thread), then push the tab
     *  to everyone (on the server thread). */
    static void refresh(MinecraftServer server) {
        if (server == null) {
            return;
        }
        CompletableFuture.runAsync(() -> {
            TierClient.Price p = TierClient.price();
            if (p != null) {
                priceUsd = p.usd();
                marketCap = p.marketCap();
            }
            server.execute(() -> {
                for (ServerPlayer pl : server.getPlayerList().getPlayers()) {
                    apply(pl);
                }
            });
        });
    }

    /** Push the current header/footer to one player (instant, from cache). */
    static void apply(ServerPlayer player) {
        player.connection.send(new ClientboundTabListPacket(HEADER, footer()));
    }

    private static Component footer() {
        MutableComponent f = Component.literal("$NDRCHST").withStyle(ChatFormatting.GREEN);
        if (!Double.isNaN(priceUsd) && priceUsd > 0) {
            f.append(Component.literal(" " + Flares.fmtPrice(priceUsd))
                    .withStyle(ChatFormatting.WHITE));
            if (!Double.isNaN(marketCap) && marketCap > 0) {
                f.append(Component.literal(" · MC ").withStyle(ChatFormatting.GRAY))
                 .append(Component.literal(Flares.fmtUsd(marketCap))
                         .withStyle(ChatFormatting.WHITE));
            }
        }
        f.append(Component.literal("\n/tier · /claim · ndrchst.com")
                .withStyle(ChatFormatting.DARK_GRAY));
        return f;
    }
}

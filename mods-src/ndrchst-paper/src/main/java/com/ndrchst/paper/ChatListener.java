package com.ndrchst.paper;

import io.papermc.paper.event.player.AsyncChatEvent;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;

/**
 * Prefix each chat line with the speaker's $NDRCHST tier badge, so the tier is
 * visible in chat (the nameplate already shows it above the head). Only linked
 * players are badged — unlinked players keep vanilla chat, so the badge always
 * means a real wallet binding rather than the holder fallback.
 */
final class ChatListener implements Listener {
    private final WalletRegistry wallets;

    ChatListener(WalletRegistry wallets) {
        this.wallets = wallets;
    }

    @EventHandler
    public void onChat(AsyncChatEvent e) {
        if (!wallets.linked(e.getPlayer().getUniqueId())) {
            return;
        }
        String tier = wallets.tier(e.getPlayer().getUniqueId());
        e.renderer((source, displayName, message, viewer) ->
                PaperTiers.badge(tier)
                        .append(Component.text(" ", NamedTextColor.GRAY))
                        .append(displayName)
                        .append(Component.text(": ", NamedTextColor.GRAY))
                        .append(message));
    }
}

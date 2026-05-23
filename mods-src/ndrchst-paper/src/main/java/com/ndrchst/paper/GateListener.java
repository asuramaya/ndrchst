package com.ndrchst.paper;

import com.ndrchst.core.IdentityGateClient;
import com.ndrchst.core.IdentityGateClient.GateResult;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.event.ClickEvent;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.AsyncPlayerPreLoginEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;

import java.util.UUID;

/**
 * Online-mode gate. The pre-login event runs OFF the main thread, so the
 * (blocking) box lookup happens there; the result is stashed and applied on
 * join. Soft gate by design: an unlinked player (or a box hiccup) still joins
 * and gets a {@code /link} prompt — the in-game link flow needs them in-world.
 * A hard "wallet to play" kick can be layered on later (config toggle).
 */
final class GateListener implements Listener {
    private final NdrchstPaperPlugin plugin;
    private final WalletRegistry wallets;

    GateListener(NdrchstPaperPlugin plugin, WalletRegistry wallets) {
        this.plugin = plugin;
        this.wallets = wallets;
    }

    @EventHandler
    public void onPreLogin(AsyncPlayerPreLoginEvent e) {
        // Floodgate (Bedrock) ids are `new UUID(0, xuid)` — MSB == 0, LSB == xuid.
        // Java online-mode uuids never have a zero MSB, so this cleanly detects a
        // Bedrock player and recovers the xuid with NO Floodgate dependency.
        UUID id = e.getUniqueId();
        String xuid = (id.getMostSignificantBits() == 0L)
                ? Long.toUnsignedString(id.getLeastSignificantBits()) : null;
        GateResult r = IdentityGateClient.gate(id.toString(), xuid, e.getName());
        if (r != null && r.ok()) {
            wallets.set(id, r.wallet(), r.tier());
        }
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent e) {
        var p = e.getPlayer();
        if (wallets.linked(p.getUniqueId())) {
            PaperTiers.apply(plugin, p, wallets.tier(p.getUniqueId()));
            p.sendMessage(PaperTiers.badge(wallets.tier(p.getUniqueId()))
                    .append(Component.text("  welcome back.", NamedTextColor.GRAY)));
        } else {
            p.sendMessage(Component.text("Link your wallet to unlock your tier — run ", NamedTextColor.GRAY)
                    .append(Component.text("/link", NamedTextColor.AQUA)
                            .clickEvent(ClickEvent.runCommand("/link"))));
        }
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent e) {
        wallets.clear(e.getPlayer().getUniqueId());
    }
}

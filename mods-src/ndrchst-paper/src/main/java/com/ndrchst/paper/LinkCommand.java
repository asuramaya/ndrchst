package com.ndrchst.paper;

import com.ndrchst.core.IdentityGateClient;
import com.ndrchst.core.IdentityGateClient.LinkStart;
import com.ndrchst.core.IdentityGateClient.LinkStatus;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.event.ClickEvent;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.scheduler.BukkitRunnable;

/**
 * {@code /link}: mint a pairing code on the box for the player's authenticated
 * identity, show them the wallet-approval link, then poll until they approve and
 * apply their tier live (no rejoin). All box I/O is off the main thread.
 */
final class LinkCommand implements CommandExecutor {
    private final NdrchstPaperPlugin plugin;
    private final WalletRegistry wallets;

    LinkCommand(NdrchstPaperPlugin plugin, WalletRegistry wallets) {
        this.plugin = plugin;
        this.wallets = wallets;
    }

    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (!(sender instanceof Player p)) {
            sender.sendMessage("Players only.");
            return true;
        }
        if (wallets.linked(p.getUniqueId())) {
            p.sendMessage(Component.text("Already linked — you're tier "
                    + wallets.tier(p.getUniqueId()) + ".", NamedTextColor.GREEN));
            return true;
        }
        final String uuid = p.getUniqueId().toString();
        final String name = p.getName();
        p.sendMessage(Component.text("Starting link…", NamedTextColor.GRAY));
        plugin.getServer().getScheduler().runTaskAsynchronously(plugin, () -> {
            LinkStart s = IdentityGateClient.linkStart(uuid, null, name);
            if (s == null) {
                p.sendMessage(Component.text("Couldn't reach ndrchst — try again.", NamedTextColor.RED));
                return;
            }
            p.sendMessage(Component.text("Open this, connect your wallet, approve:", NamedTextColor.GRAY));
            p.sendMessage(Component.text(s.verifyUrl(), NamedTextColor.AQUA)
                    .clickEvent(ClickEvent.openUrl(s.verifyUrl())));
            p.sendMessage(Component.text("Code: " + s.userCode(), NamedTextColor.YELLOW));
            pollUntilLinked(p, s.pairId());
        });
        return true;
    }

    private void pollUntilLinked(Player p, String pairId) {
        new BukkitRunnable() {
            int tries = 0;

            @Override
            public void run() {
                if (++tries > 150 || !p.isOnline()) {  // ~5 min at 2s
                    cancel();
                    return;
                }
                LinkStatus st = IdentityGateClient.linkPoll(pairId);
                if (st != null && "approved".equals(st.status())) {
                    cancel();
                    wallets.set(p.getUniqueId(), st.wallet(), st.tier());
                    plugin.getServer().getScheduler().runTask(plugin, () -> {
                        if (p.isOnline()) {
                            PaperTiers.apply(plugin, p, st.tier());
                            p.sendMessage(Component.text("Linked! You're tier "
                                    + st.tier() + ".", NamedTextColor.GREEN));
                        }
                    });
                }
            }
        }.runTaskTimerAsynchronously(plugin, 40L, 40L);  // poll every 2s
    }
}

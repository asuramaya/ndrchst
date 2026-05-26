package com.ndrchst.paper;

import com.ndrchst.core.TierClient;
import com.ndrchst.core.TierClient.Price;
import com.ndrchst.core.TierClient.Standing;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.event.ClickEvent;
import net.kyori.adventure.text.event.HoverEvent;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;

/**
 * The read-only command surface: {@code /ndrchst} (the front door — instant
 * status + what every command does), {@code /tier} (your standing), and
 * {@code /price} (the cached ticker). {@code /tier} and {@code /price} read the
 * box via the shared {@link TierClient} off the main thread; {@code /ndrchst} is
 * instant (session cache only) so the help panel never waits on the box.
 */
final class InfoCommands implements CommandExecutor {
    private final NdrchstPaperPlugin plugin;
    private final WalletRegistry wallets;

    InfoCommands(NdrchstPaperPlugin plugin, WalletRegistry wallets) {
        this.plugin = plugin;
        this.wallets = wallets;
    }

    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        String name = cmd.getName().toLowerCase();

        if (name.equals("ndrchst")) {
            if (!(sender instanceof Player p)) {
                sender.sendMessage("Players only.");
                return true;
            }
            overview(p);
            return true;
        }

        if (name.equals("price")) {
            plugin.getServer().getScheduler().runTaskAsynchronously(plugin, () -> {
                Price pr = TierClient.price();
                if (pr == null) {
                    sender.sendMessage(Component.text("Ticker unavailable — try again.", NamedTextColor.GRAY));
                    return;
                }
                String mc = Double.isNaN(pr.marketCap()) ? "?"
                        : String.format("$%,.0f", pr.marketCap());
                sender.sendMessage(Component.text(
                        String.format("$NDRCHST  $%.8f  ·  MC %s", pr.usd(), mc), NamedTextColor.GOLD));
            });
            return true;
        }

        // /tier
        if (!(sender instanceof Player p)) {
            sender.sendMessage("Players only.");
            return true;
        }
        String wallet = wallets.wallet(p.getUniqueId());
        if (wallet == null) {
            p.sendMessage(Component.text("Not linked yet — ", NamedTextColor.GRAY)
                    .append(cmd("/link", "bind your wallet to unlock your tier"))
                    .append(Component.text(".", NamedTextColor.GRAY)));
            return true;
        }
        plugin.getServer().getScheduler().runTaskAsynchronously(plugin, () -> {
            Standing s = TierClient.lookup(wallet);
            if (s == null) {
                p.sendMessage(Component.text("Couldn't reach ndrchst — try again.", NamedTextColor.RED));
                return;
            }
            p.sendMessage(PaperTiers.badge(s.tier()).append(
                    Component.text(String.format("   %.4f%% of supply", s.pct()), NamedTextColor.GRAY)));
            if (s.nextKey() != null) {
                p.sendMessage(Component.text(
                        String.format("Next: %s at %.2f%% — hold more $NDRCHST.", s.nextName(), s.nextPct()),
                        NamedTextColor.DARK_GRAY));
            } else {
                p.sendMessage(Component.text("Top of the ladder. ✦", NamedTextColor.DARK_GRAY));
            }
        });
        return true;
    }

    /** The /ndrchst front door: instant (session cache), shows standing + the
     *  whole clickable command surface so nothing is hidden. */
    private void overview(Player p) {
        p.sendMessage(Component.text("───── ", NamedTextColor.DARK_GRAY)
                .append(Component.text("$NDRCHST", NamedTextColor.GOLD))
                .append(Component.text(" ─────", NamedTextColor.DARK_GRAY)));
        if (wallets.linked(p.getUniqueId())) {
            p.sendMessage(PaperTiers.badge(wallets.tier(p.getUniqueId()))
                    .append(Component.text("  ·  ", NamedTextColor.DARK_GRAY))
                    .append(cmd("/tier", "your exact holdings & next tier")));
        } else {
            p.sendMessage(Component.text("Not linked — ", NamedTextColor.GRAY)
                    .append(cmd("/link", "bind your wallet to unlock your tier"))
                    .append(Component.text(" to unlock your tier.", NamedTextColor.GRAY)));
        }
        p.sendMessage(Component.text("Info   ", NamedTextColor.DARK_GRAY)
                .append(cmd("/tier", "your standing")).append(gap())
                .append(cmd("/price", "live $NDRCHST ticker")));
        p.sendMessage(Component.text("Perks  ", NamedTextColor.DARK_GRAY)
                .append(Component.text("/hat", NamedTextColor.WHITE))
                .append(Component.text(" Silver+", NamedTextColor.DARK_GRAY)).append(gap())
                .append(Component.text("/fly", NamedTextColor.YELLOW))
                .append(Component.text(" Gold+", NamedTextColor.DARK_GRAY)));
    }

    /** A clickable, hover-described command chip. */
    private static Component cmd(String command, String hover) {
        return Component.text(command, NamedTextColor.AQUA)
                .clickEvent(ClickEvent.runCommand(command))
                .hoverEvent(HoverEvent.showText(Component.text(hover, NamedTextColor.GRAY)));
    }

    private static Component gap() {
        return Component.text("   ", NamedTextColor.DARK_GRAY);
    }
}

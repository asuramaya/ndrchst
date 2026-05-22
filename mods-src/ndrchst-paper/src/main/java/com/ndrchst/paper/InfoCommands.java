package com.ndrchst.paper;

import com.ndrchst.core.TierClient;
import com.ndrchst.core.TierClient.Price;
import com.ndrchst.core.TierClient.Standing;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;

/**
 * {@code /tier} (your standing) and {@code /price} (the cached ticker). Both read
 * the box via the shared {@link TierClient} off the main thread; {@code /price}
 * needs no wallet, {@code /tier} uses the player's linked wallet.
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
        if (cmd.getName().equalsIgnoreCase("price")) {
            plugin.getServer().getScheduler().runTaskAsynchronously(plugin, () -> {
                Price pr = TierClient.price();
                if (pr == null) {
                    sender.sendMessage(Component.text("Ticker unavailable.", NamedTextColor.GRAY));
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
            p.sendMessage(Component.text("Not linked yet — run /link.", NamedTextColor.GRAY));
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
                        String.format("Next: %s at %.2f%%", s.nextName(), s.nextPct()),
                        NamedTextColor.DARK_GRAY));
            }
        });
        return true;
    }
}

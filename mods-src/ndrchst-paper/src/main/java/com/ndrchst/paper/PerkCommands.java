package com.ndrchst.paper;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Material;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;

/**
 * Tier-gated gameplay perks, enforced by the LuckPerms permission nodes the tier
 * groups grant ({@code ndrchst.perk.fly} = gold+, {@code ndrchst.perk.hat} =
 * silver+, inherited up the chain). The plugin OWNS these — no EssentialsX or
 * other third-party dependency — so the cross-play path delivers real perks off
 * the same wallet gate as the modded server.
 */
final class PerkCommands implements CommandExecutor {
    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (!(sender instanceof Player p)) {
            sender.sendMessage("Players only.");
            return true;
        }
        return switch (cmd.getName().toLowerCase()) {
            case "fly" -> fly(p);
            case "hat" -> hat(p);
            default -> false;
        };
    }

    private boolean fly(Player p) {
        if (!p.hasPermission("ndrchst.perk.fly")) {
            return deny(p, "Gold");
        }
        boolean on = !p.getAllowFlight();
        p.setAllowFlight(on);
        if (!on) {
            p.setFlying(false);
        }
        p.sendMessage(Component.text("Flight " + (on ? "enabled" : "disabled") + ".",
                on ? NamedTextColor.AQUA : NamedTextColor.GRAY));
        return true;
    }

    private boolean hat(Player p) {
        if (!p.hasPermission("ndrchst.perk.hat")) {
            return deny(p, "Silver");
        }
        PlayerInventory inv = p.getInventory();
        ItemStack hand = inv.getItemInMainHand();
        if (hand.getType() == Material.AIR) {
            p.sendMessage(Component.text("Hold an item to wear it as a hat.", NamedTextColor.GRAY));
            return true;
        }
        ItemStack helmet = inv.getHelmet();
        inv.setHelmet(hand);
        inv.setItemInMainHand(helmet);  // previous helmet (or empty) back to hand
        p.sendMessage(Component.text("Hat on.", NamedTextColor.LIGHT_PURPLE));
        return true;
    }

    private boolean deny(Player p, String tier) {
        p.sendMessage(Component.text("That perk unlocks at ", NamedTextColor.GRAY)
                .append(Component.text(tier, NamedTextColor.YELLOW))
                .append(Component.text(" — hold more $NDRCHST.  ", NamedTextColor.GRAY))
                .append(Component.text("/tier", NamedTextColor.AQUA)));
        return true;
    }
}

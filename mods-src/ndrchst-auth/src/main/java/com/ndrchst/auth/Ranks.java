package com.ndrchst.auth;

import java.util.List;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * Sets a player's rank from their holdings tier using <b>FTB Ranks</b> — the
 * permissions/ranks mod ATM10 already ships (alongside FTB Essentials/Chunks,
 * which is where the per-rank gameplay perks come from). We assign by
 * dispatching the server command rather than linking an API, so this stays
 * decoupled: if FTB Ranks isn't present the command simply no-ops (the gate
 * still held).
 *
 * <p>The six tier ids double as the FTB Ranks rank ids (holder … whale); their
 * prefixes + perk permission nodes live in world/serverconfig/ftbranks.
 */
final class Ranks {
    private Ranks() {}

    /** The holdings tiers, ascending — also the FTB Ranks rank ids. Shared with
     *  the badge/flare layer so the ladder is defined once. */
    static final List<String> TIERS = Tiers.ORDER;

    static void apply(ServerPlayer player, String tier) {
        MinecraftServer server = player.getServer();
        if (server == null) {
            return;
        }
        String name = player.getGameProfile().getName();
        CommandSourceStack src = server.createCommandSourceStack().withPermission(4)
                .withSuppressedOutput();
        var commands = server.getCommands();
        // Single active tier: drop the other tier ranks, then add the current.
        for (String t : TIERS) {
            if (!t.equals(tier)) {
                commands.performPrefixedCommand(src, "ftbranks remove " + name + " " + t);
            }
        }
        commands.performPrefixedCommand(src, "ftbranks add " + name + " " + tier);
    }
}

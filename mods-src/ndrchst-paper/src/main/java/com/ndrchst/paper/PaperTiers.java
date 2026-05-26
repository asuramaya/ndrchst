package com.ndrchst.paper;

import com.ndrchst.core.Tier;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;
import org.bukkit.entity.Player;
import org.bukkit.scoreboard.Scoreboard;
import org.bukkit.scoreboard.Team;

/**
 * Paper/Adventure adapter over the shared {@link Tier} model — the cross-play
 * counterpart to the mod's {@code Tiers}. Maps the neutral legacy colour code to
 * Adventure {@code NamedTextColor}, builds the badge, and pins a player's
 * nameplate/tab colour via a scoreboard team (mirrors the mod's TierTeams).
 */
final class PaperTiers {
    private PaperTiers() {}

    static NamedTextColor color(char legacy) {
        return switch (legacy) {
            case '6' -> NamedTextColor.GOLD;
            case 'f' -> NamedTextColor.WHITE;
            case 'e' -> NamedTextColor.YELLOW;
            case 'b' -> NamedTextColor.AQUA;
            case 'd' -> NamedTextColor.LIGHT_PURPLE;
            default  -> NamedTextColor.GRAY;
        };
    }

    /** The coloured "◆ Diamond" emblem, shared by /tier + flares. */
    static Component badge(String tier) {
        Tier.Style s = Tier.of(tier);
        Component c = Component.text(s.glyph() + " " + s.label(), color(s.legacyColor()));
        return s.bold() ? c.decorate(TextDecoration.BOLD) : c;
    }

    /** Apply a player's tier: nameplate/tab colour (scoreboard team) + LuckPerms
     *  group (dispatched, no API link — no-op if LuckPerms absent) + tab footer. */
    static void apply(NdrchstPaperPlugin plugin, Player p, String tier) {
        Tier.Style s = Tier.of(tier);
        NamedTextColor col = color(s.legacyColor());

        Scoreboard sb = plugin.getServer().getScoreboardManager().getMainScoreboard();
        String teamName = "ndrchst_" + s.key();
        Team team = sb.getTeam(teamName);
        if (team == null) {
            team = sb.registerNewTeam(teamName);
        }
        team.color(col);
        team.prefix(Component.text(s.glyph() + " ", col));
        // Single active tier: drop the player from any other ndrchst team first.
        for (String key : Tier.ORDER) {
            Team t = sb.getTeam("ndrchst_" + key);
            if (t != null) {
                t.removeEntry(p.getName());
            }
        }
        team.addEntry(p.getName());

        // Tier perks (claims/land/etc.) via LuckPerms, dispatched like the modded
        // path's `ftbranks add` — no compile-time dependency, no-op if absent.
        plugin.getServer().dispatchCommand(plugin.getServer().getConsoleSender(),
                "lp user " + p.getName() + " parent set ndrchst_" + s.key());

        p.sendPlayerListFooter(Component.text(
                "$NDRCHST  ·  /ndrchst for status & commands", NamedTextColor.DARK_GRAY));
    }
}

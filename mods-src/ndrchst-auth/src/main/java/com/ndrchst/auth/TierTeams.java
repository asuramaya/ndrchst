package com.ndrchst.auth;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.scores.PlayerTeam;
import net.minecraft.world.scores.Scoreboard;
import net.minecraft.world.scores.Team;

/**
 * Reflects a player's tier on their <b>gamertag everywhere vanilla derives the
 * display name from</b> — the floating nameplate above the head, the tab list,
 * and the chat sender — by putting them on a per-tier scoreboard team (colored
 * name + a compact glyph prefix). This is what FTB Ranks' {@code name_format}
 * was meant to do but doesn't reliably reach in ATM10; a vanilla team does,
 * because {@code getDisplayName()} (used by all three) applies team formatting.
 *
 * <p>Cosmetic ONLY: friendly fire and collision are pinned to vanilla defaults
 * so tier membership never changes PvP/combat. Costs 0 RPC — the tier comes from
 * the verified login result. The full tier word lives in {@code /tier} + the web
 * ranks page; the nameplate stays compact with just the colored glyph.
 */
final class TierTeams {
    private TierTeams() {}

    private static final String PREFIX = "ndrchst_";

    /** Put the player on their tier team (creating/refreshing it). Idempotent;
     *  moving tiers (op {@code rank set}) just re-assigns. */
    static void assign(MinecraftServer server, ServerPlayer player, String tier) {
        if (server == null || tier == null) {
            return;
        }
        Scoreboard sb = server.getScoreboard();
        String teamName = PREFIX + tier;
        PlayerTeam team = sb.getPlayerTeam(teamName);
        if (team == null) {
            team = sb.addPlayerTeam(teamName);
        }
        Tiers.Style s = Tiers.style(tier);
        team.setColor(s.color());                 // colors the name in chat/tab/nameplate
        MutableComponent prefix = Component.literal(s.glyph() + " ").withStyle(s.color());
        if (s.bold()) {
            prefix = prefix.withStyle(ChatFormatting.BOLD);
        }
        team.setPlayerPrefix(prefix);
        // Pin to vanilla defaults so this stays purely cosmetic.
        team.setAllowFriendlyFire(true);
        team.setSeeFriendlyInvisibles(false);
        team.setCollisionRule(Team.CollisionRule.ALWAYS);
        team.setNameTagVisibility(Team.Visibility.ALWAYS);
        sb.addPlayerToTeam(player.getGameProfile().getName(), team);
    }
}

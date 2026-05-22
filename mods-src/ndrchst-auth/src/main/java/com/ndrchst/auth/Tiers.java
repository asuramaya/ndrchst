package com.ndrchst.auth;

import java.util.List;
import java.util.Map;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;

/**
 * In-game presentation for the holdings tiers — the visual identity layer that
 * makes holding $NDRCHST legible in chat, the tab list, join flares and
 * {@code /tier}.
 *
 * <p><b>This is purely cosmetic.</b> The thresholds that DECIDE a tier live on
 * the box (the single source of truth — see {@code domain/wallet.py}); the mod
 * never recomputes them, it only renders whatever tier the box already assigned.
 * The six keys here match the box tier ids and the FTB Ranks rank ids, and the
 * colours/glyphs mirror {@code deploy/server-config/ftbranks-ranks.snbt} so the
 * chat-prefix badge and the flare badge look identical.
 */
final class Tiers {
    private Tiers() {}

    record Style(String name, ChatFormatting color, boolean bold, String glyph) {}

    /** Ascending prestige — also the FTB Ranks rank ids and box tier keys. */
    static final List<String> ORDER =
            List.of("holder", "bronze", "silver", "gold", "diamond", "whale");

    // Glyphs are BMP symbols MC's unicode font renders; escalate with prestige.
    private static final Map<String, Style> STYLES = Map.of(
            "holder",  new Style("Holder",  ChatFormatting.GRAY,         false, "◆"), // ◆
            "bronze",  new Style("Bronze",  ChatFormatting.GOLD,         false, "◆"), // ◆
            "silver",  new Style("Silver",  ChatFormatting.WHITE,        false, "◆"), // ◆
            "gold",    new Style("Gold",    ChatFormatting.YELLOW,       false, "★"), // ★
            "diamond", new Style("Diamond", ChatFormatting.AQUA,         false, "❖"), // ❖
            "whale",   new Style("Whale",   ChatFormatting.LIGHT_PURPLE, true,  "✦")  // ✦
    );

    static Style style(String tier) {
        return STYLES.getOrDefault(tier == null ? "holder" : tier, STYLES.get("holder"));
    }

    /** Ladder index (0 = holder … 5 = whale); -1-safe → 0. */
    static int rank(String tier) {
        int i = ORDER.indexOf(tier);
        return i < 0 ? 0 : i;
    }

    /** The coloured "◆ Diamond" emblem reused across flares + {@code /tier}. */
    static MutableComponent badge(String tier) {
        Style s = style(tier);
        MutableComponent c =
                Component.literal(s.glyph() + " " + s.name()).withStyle(s.color());
        return s.bold() ? c.withStyle(ChatFormatting.BOLD) : c;
    }
}

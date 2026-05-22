package com.ndrchst.core;

import java.util.List;
import java.util.Map;

/**
 * Platform-neutral holdings-tier model — the single source of tier identity
 * shared by every in-game adapter (the NeoForge mod and the Paper plugin), so
 * the palette is defined exactly once.
 *
 * <p>Purely cosmetic data: the thresholds that DECIDE a tier live on the box
 * (the single source of truth — {@code domain/wallet.py}); this only carries how
 * a tier LOOKS. Colour is the legacy Minecraft formatting code char (e.g. '6' =
 * gold), which both {@code ChatFormatting} (NeoForge) and Adventure / §-codes
 * (Paper) map trivially — and it already matches the &-codes in
 * {@code deploy/server-config/ftbranks-ranks.snbt}.
 */
public final class Tier {
    private Tier() {}

    /** One tier's presentation. legacyColor is a Minecraft colour code char. */
    public record Style(String key, String label, char legacyColor, boolean bold, String glyph) {}

    /** Ascending prestige — also the FTB Ranks rank ids and box tier keys. */
    public static final List<String> ORDER =
            List.of("holder", "bronze", "silver", "gold", "diamond", "whale");

    // Glyphs are BMP symbols MC's unicode font renders; escalate with prestige.
    private static final Map<String, Style> STYLES = Map.of(
            "holder",  new Style("holder",  "Holder",  '7', false, "◆"),
            "bronze",  new Style("bronze",  "Bronze",  '6', false, "◆"),
            "silver",  new Style("silver",  "Silver",  'f', false, "◆"),
            "gold",    new Style("gold",    "Gold",    'e', false, "★"),
            "diamond", new Style("diamond", "Diamond", 'b', false, "❖"),
            "whale",   new Style("whale",   "Whale",   'd', true,  "✦"));

    /** Presentation for a tier key; unknown/null → holder. */
    public static Style of(String key) {
        return STYLES.getOrDefault(key == null ? "holder" : key, STYLES.get("holder"));
    }

    /** Ladder index (0 = holder … 5 = whale); unknown → 0. */
    public static int rank(String key) {
        int i = ORDER.indexOf(key);
        return i < 0 ? 0 : i;
    }
}

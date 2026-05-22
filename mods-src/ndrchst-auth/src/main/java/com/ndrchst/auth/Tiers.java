package com.ndrchst.auth;

import com.ndrchst.core.Tier;
import java.util.List;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;

/**
 * NeoForge adapter over the platform-neutral {@link Tier} model: maps the shared
 * tier palette to Minecraft {@code ChatFormatting} + {@code Component} for chat,
 * the tab list, join flares and {@code /tier}. The data lives once in core; this
 * only renders it (the Paper plugin has its own Adventure-based adapter).
 */
final class Tiers {
    private Tiers() {}

    record Style(String name, ChatFormatting color, boolean bold, String glyph) {}

    /** Ascending prestige — delegates to the shared core ladder. */
    static final List<String> ORDER = Tier.ORDER;

    static Style style(String tier) {
        Tier.Style s = Tier.of(tier);
        ChatFormatting color = ChatFormatting.getByCode(s.legacyColor());
        return new Style(s.label(), color == null ? ChatFormatting.GRAY : color,
                s.bold(), s.glyph());
    }

    /** Ladder index (0 = holder … 5 = whale); -1-safe → 0. */
    static int rank(String tier) {
        return Tier.rank(tier);
    }

    /** The coloured "◆ Diamond" emblem reused across flares + {@code /tier}. */
    static MutableComponent badge(String tier) {
        Style s = style(tier);
        MutableComponent c =
                Component.literal(s.glyph() + " " + s.name()).withStyle(s.color());
        return s.bold() ? c.withStyle(ChatFormatting.BOLD) : c;
    }
}

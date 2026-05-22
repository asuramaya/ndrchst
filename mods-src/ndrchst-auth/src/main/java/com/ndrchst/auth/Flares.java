package com.ndrchst.auth;

import java.util.ArrayList;
import java.util.List;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;

/**
 * Builds the chat surfaces that make a holder's tier visible in-game: a
 * personal welcome on join, a server-wide arrival flare for paying tiers, and
 * the {@code /tier} standing readout. All presentation — no network, no state.
 */
final class Flares {
    private Flares() {}

    private static MutableComponent grey(String s) {
        return Component.literal(s).withStyle(ChatFormatting.GRAY);
    }

    private static MutableComponent white(String s) {
        return Component.literal(s).withStyle(ChatFormatting.WHITE);
    }

    /** Shown to the joining player: their badge + the two things to do next. */
    static Component welcome(String tier) {
        return Component.empty()
                .append(Tiers.badge(tier))
                .append(grey(" — welcome. "))
                .append(white("/claim"))
                .append(grey(" for your crate, "))
                .append(white("/tier"))
                .append(grey(" for your standing."));
    }

    /** Broadcast to everyone when a bronze+ holder arrives — social proof that
     *  holding shows up. (Holder tier rides the vanilla join line only.) */
    static Component arrival(String name, String tier) {
        return Component.empty()
                .append(Tiers.badge(tier))
                .append(grey(" "))
                .append(white(name))
                .append(grey(" joined the realm."));
    }

    /** The {@code /tier} readout: tier, holdings %, and the climb to the next
     *  rung — the token→rank loop made legible at a glance. */
    static List<Component> standing(TierClient.Standing s) {
        List<Component> lines = new ArrayList<>();
        lines.add(grey("─── ").append(white("Your $NDRCHST standing"))
                .append(grey(" ───")));
        lines.add(grey("Tier: ").append(Tiers.badge(s.tier())));
        lines.add(grey("Holdings: ").append(white(pct(s.pct())))
                .append(grey(" of supply")));
        if (s.nextKey() == null) {
            lines.add(Component.literal("Top of the ladder — nothing left to climb.")
                    .withStyle(ChatFormatting.LIGHT_PURPLE));
        } else {
            double more = Math.max(0.0, s.nextPct() - s.pct());
            lines.add(grey("Next: ").append(Tiers.badge(s.nextKey()))
                    .append(grey(" at ")).append(white(pct(s.nextPct())))
                    .append(grey(" — hold ")).append(white(pct(more)))
                    .append(grey(" more")));
        }
        // Land: protect + force-load chunks (24/7 automation) — scales with tier.
        lines.add(grey("Land: press ").append(white("C"))
                .append(grey(" to claim — force-load scales with your tier")));
        Component price = priceLine(s);
        if (price != null) {
            lines.add(price);
        }
        lines.add(grey("/claim").append(grey("  •  ")).append(grey("ndrchst.com/ranks")));
        return lines;
    }

    /** The cached $NDRCHST ticker line (price · market cap), or null when the
     *  box has no cached value. Pure decoration — not real-time. */
    static Component priceLine(TierClient.Standing s) {
        if (Double.isNaN(s.priceUsd()) || s.priceUsd() <= 0) {
            return null;
        }
        MutableComponent c = Component.literal("$NDRCHST ").withStyle(ChatFormatting.GREEN)
                .append(white(fmtPrice(s.priceUsd())));
        if (!Double.isNaN(s.marketCap()) && s.marketCap() > 0) {
            c.append(grey(" · MC ")).append(white(fmtUsd(s.marketCap())));
        }
        return c;
    }

    private static String pct(double v) {
        return String.format(java.util.Locale.ROOT, "%.2f%%", v);
    }

    /** USD price formatter, shared with the tab-menu ticker. */
    static String fmtPrice(double v) {
        if (v >= 1) {
            return String.format(java.util.Locale.ROOT, "$%,.2f", v);
        }
        if (v >= 0.01) {
            return String.format(java.util.Locale.ROOT, "$%.4f", v);
        }
        return String.format(java.util.Locale.ROOT, "$%.8f", v);
    }

    /** Compact market-cap formatter ($X.XM / $XXXK), shared with the tab menu. */
    static String fmtUsd(double v) {
        if (v >= 1e9) {
            return String.format(java.util.Locale.ROOT, "$%.1fB", v / 1e9);
        }
        if (v >= 1e6) {
            return String.format(java.util.Locale.ROOT, "$%.1fM", v / 1e6);
        }
        if (v >= 1e3) {
            return String.format(java.util.Locale.ROOT, "$%.0fK", v / 1e3);
        }
        return String.format(java.util.Locale.ROOT, "$%.0f", v);
    }
}

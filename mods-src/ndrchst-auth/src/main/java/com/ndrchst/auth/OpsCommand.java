package com.ndrchst.auth;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.builder.RequiredArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;
import java.util.List;
import java.util.Map;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.arguments.EntityArgument;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * {@code /ndrchst …} — the operator surface (permission level 2). Lets the
 * operator run the economy live without a redeploy:
 *
 * <ul>
 *   <li>{@code claim reset <player>} — clear a player's crate cooldown.</li>
 *   <li>{@code reward <player> <tier>} / {@code reward all <tier>} — grant a
 *       tier crate now (comp / event drop), bypassing the cooldown.</li>
 *   <li>{@code rank set <player> <tier>} — force a player's rank.</li>
 *   <li>{@code config} / {@code config <key> <value>} — read/retune the box
 *       runtime knobs (daily cooldown, snapshot + price refresh cadences).</li>
 * </ul>
 *
 * Box-backed actions (claim reset, config) are double-gated: op-only here, and
 * bridge-only at the box. Reward + rank are dispatched as server commands, so
 * they no-op gracefully if a backing system (loot table / FTB Ranks) is absent.
 */
final class OpsCommand {
    private OpsCommand() {}

    /** Knob keys offered as command literals — mirror the box op_config.KNOBS;
     *  the box is authoritative and validates/clamps. */
    private static final List<String> KNOBS =
            List.of("daily_cooldown_s", "snapshot_interval_s", "price_interval_s");

    static void register(CommandDispatcher<CommandSourceStack> d) {
        LiteralArgumentBuilder<CommandSourceStack> root =
                Commands.literal("ndrchst").requires(s -> s.hasPermission(2));

        // claim reset <player>
        root.then(Commands.literal("claim").then(Commands.literal("reset")
                .then(Commands.argument("player", EntityArgument.player())
                        .executes(OpsCommand::dailyReset))));

        // reward <player> <tier> | reward all <tier>
        RequiredArgumentBuilder<CommandSourceStack, ?> rewardPlayer =
                Commands.argument("player", EntityArgument.player());
        LiteralArgumentBuilder<CommandSourceStack> rewardAll = Commands.literal("all");
        RequiredArgumentBuilder<CommandSourceStack, ?> rankPlayer =
                Commands.argument("player", EntityArgument.player());
        for (String tier : Tiers.ORDER) {
            rewardPlayer.then(Commands.literal(tier).executes(c -> rewardOne(c, tier)));
            rewardAll.then(Commands.literal(tier).executes(c -> rewardEveryone(c, tier)));
            rankPlayer.then(Commands.literal(tier).executes(c -> rankSet(c, tier)));
        }
        root.then(Commands.literal("reward").then(rewardPlayer).then(rewardAll));
        root.then(Commands.literal("rank").then(Commands.literal("set").then(rankPlayer)));

        // config | config <key> <value>
        LiteralArgumentBuilder<CommandSourceStack> config =
                Commands.literal("config").executes(OpsCommand::configList);
        for (String key : KNOBS) {
            config.then(Commands.literal(key).then(
                    Commands.argument("value", IntegerArgumentType.integer(0))
                            .executes(c -> configSet(c, key))));
        }
        root.then(config);

        d.register(root);
    }

    // ── rewards (mod-local; dispatch the vanilla loot command) ──────────────
    private static void giveCrate(MinecraftServer server, String name, String tier) {
        server.getCommands().performPrefixedCommand(
                server.createCommandSourceStack().withSuppressedOutput().withPermission(4),
                "loot give " + name + " loot ndrchst:daily/" + tier);
    }

    private static int rewardOne(CommandContext<CommandSourceStack> ctx, String tier) {
        try {
            ServerPlayer target = EntityArgument.getPlayer(ctx, "player");
            MinecraftServer server = ctx.getSource().getServer();
            String name = target.getGameProfile().getName();
            giveCrate(server, name, tier);
            ctx.getSource().sendSuccess(
                    () -> Component.literal("Gave a " + tier + " crate to " + name), true);
            return 1;
        } catch (Exception e) {
            ctx.getSource().sendFailure(Component.literal("Player not found."));
            return 0;
        }
    }

    private static int rewardEveryone(CommandContext<CommandSourceStack> ctx, String tier) {
        MinecraftServer server = ctx.getSource().getServer();
        List<ServerPlayer> players = server.getPlayerList().getPlayers();
        for (ServerPlayer p : players) {
            giveCrate(server, p.getGameProfile().getName(), tier);
        }
        int n = players.size();
        ctx.getSource().sendSuccess(
                () -> Component.literal("Gave a " + tier + " crate to " + n + " player"
                        + (n == 1 ? "" : "s")), true);
        return 1;
    }

    // ── rank override (mod-local; FTB Ranks via Ranks.apply) ────────────────
    private static int rankSet(CommandContext<CommandSourceStack> ctx, String tier) {
        try {
            ServerPlayer target = EntityArgument.getPlayer(ctx, "player");
            String name = target.getGameProfile().getName();
            Ranks.apply(target, tier);
            TierTeams.assign(ctx.getSource().getServer(), target, tier);  // update gamertag
            ctx.getSource().sendSuccess(
                    () -> Component.literal("Set " + name + "'s rank to " + tier), true);
            return 1;
        } catch (Exception e) {
            ctx.getSource().sendFailure(Component.literal(
                    "ndrchst — rank set failed (" + e + ")."));
            return 0;
        }
    }

    // ── claim reset (box-backed; bridge) ────────────────────────────────────
    private static int dailyReset(CommandContext<CommandSourceStack> ctx) {
        try {
            ServerPlayer target = EntityArgument.getPlayer(ctx, "player");
            String wallet = DailyCommand.WALLET.get(target.getUUID());
            if (wallet == null) {
                ctx.getSource().sendFailure(Component.literal(
                        "ndrchst — that player hasn't signed in this session."));
                return 0;
            }
            String name = target.getGameProfile().getName();
            if (DailyClient.reset(wallet)) {
                ctx.getSource().sendSuccess(
                        () -> Component.literal("Reset crate cooldown for " + name), true);
                return 1;
            }
            ctx.getSource().sendFailure(Component.literal(
                    "ndrchst — reset failed (couldn't reach the server)."));
            return 0;
        } catch (Exception e) {
            ctx.getSource().sendFailure(Component.literal("Player not found."));
            return 0;
        }
    }

    // ── runtime config (box-backed; bridge) ─────────────────────────────────
    private static int configList(CommandContext<CommandSourceStack> ctx) {
        CommandSourceStack src = ctx.getSource();
        Map<String, Integer> cfg = OpsClient.listConfig();
        if (cfg == null) {
            src.sendFailure(Component.literal("ndrchst — couldn't reach the server."));
            return 0;
        }
        src.sendSuccess(() -> Component.literal("ndrchst runtime config:")
                .withStyle(ChatFormatting.GRAY), false);
        for (Map.Entry<String, Integer> e : cfg.entrySet()) {
            String line = "  " + e.getKey() + " = " + e.getValue()
                    + "  (" + humanize(e.getValue()) + ")";
            src.sendSuccess(() -> Component.literal(line), false);
        }
        return 1;
    }

    private static int configSet(CommandContext<CommandSourceStack> ctx, String key) {
        int value = IntegerArgumentType.getInteger(ctx, "value");
        Integer applied = OpsClient.setConfig(key, value);
        if (applied == null) {
            ctx.getSource().sendFailure(Component.literal(
                    "ndrchst — couldn't set " + key + " (server unreachable)."));
            return 0;
        }
        int a = applied;
        ctx.getSource().sendSuccess(() -> Component.literal(
                key + " set to " + a + "  (" + humanize(a) + ")"), true);
        return 1;
    }

    /** Render a seconds value as a friendly duration for the config readout. */
    private static String humanize(int s) {
        if (s <= 0) {
            return "off";
        }
        if (s % 3600 == 0) {
            return (s / 3600) + "h";
        }
        if (s % 60 == 0) {
            return (s / 60) + "m";
        }
        return s + "s";
    }
}

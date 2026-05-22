package com.ndrchst.auth;

import com.mojang.authlib.GameProfile;
import com.mojang.logging.LogUtils;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.RegisterCommandsEvent;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import net.neoforged.neoforge.network.event.RegisterConfigurationTasksEvent;
import net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent;
import net.neoforged.neoforge.network.handling.IPayloadContext;
import net.neoforged.neoforge.network.registration.HandlerThread;
import net.neoforged.neoforge.network.registration.PayloadRegistrar;
import org.slf4j.Logger;

/**
 * ndrchst wallet-auth mod — the cryptographic gate for the server.
 *
 * <p>During the connection configuration phase the server asks for a join
 * token (minted by the box after Sign-In-With-Solana, written into the game
 * dir by the client). The client replies with it; the server verifies it via
 * {@link JoinVerifier} and either admits the player (stashing their wallet +
 * tier for rank assignment at login, M5) or disconnects. The payload channel
 * is non-optional, so a client without this mod is denied during negotiation —
 * the client is the only way in.
 */
@Mod(NdrchstAuth.MOD_ID)
public final class NdrchstAuth {
    public static final String MOD_ID = "ndrchst_auth";
    private static final Logger LOG = LogUtils.getLogger();
    private static final String TOKEN_FILE = "ndrchst-join.token";

    /** Wallet identity verified at config time, keyed by player UUID; consumed
     *  when the player finishes logging in (M5 rank assignment). */
    public static final Map<UUID, JoinVerifier.Result> VERIFIED = new ConcurrentHashMap<>();

    public NdrchstAuth(IEventBus modBus) {
        // RegisterPayloadHandlersEvent + RegisterConfigurationTasksEvent are
        // BOTH mod-bus events (IModBusEvent). PlayerLoggedInEvent is a game-bus
        // event. Getting the bus wrong is a fatal ModLoadingException.
        modBus.addListener(NdrchstAuth::onRegisterPayloads);
        modBus.addListener(NdrchstAuth::onRegisterConfigTasks);
        NeoForge.EVENT_BUS.addListener(NdrchstAuth::onPlayerLogin);
        NeoForge.EVENT_BUS.addListener(NdrchstAuth::onRegisterCommands);
        NeoForge.EVENT_BUS.addListener(NdrchstAuth::onServerTick);
        LOG.info("[ndrchst-auth] loaded — wallet-gated join + ranks + /daily");
    }

    /** Slow tab-menu ticker refresh: prime it ~10s after boot, then every ~5 min.
     *  The actual fetch + push happens off the tick (TabList.refresh). */
    private static long tickCount = 0;
    private static void onServerTick(ServerTickEvent.Post event) {
        long c = ++tickCount;
        if (c == 200L || c % 6000L == 0L) {
            TabList.refresh(event.getServer());
        }
    }

    private static void onRegisterCommands(RegisterCommandsEvent event) {
        DailyCommand.register(event.getDispatcher());
        TierCommand.register(event.getDispatcher());
        OpsCommand.register(event.getDispatcher());
    }

    /** Once the verified player is in, set their FTB Ranks rank from the tier
     *  and surface that tier: a personal welcome + (for paying tiers) a
     *  server-wide arrival flare. */
    private static void onPlayerLogin(PlayerEvent.PlayerLoggedInEvent event) {
        JoinVerifier.Result res = VERIFIED.remove(event.getEntity().getUUID());
        if (res == null || res.tier() == null) {
            return;
        }
        if (!(event.getEntity() instanceof ServerPlayer player)) {
            return;
        }
        // Stash the verified wallet so /daily + /tier can ask the box (it holds
        // the authoritative cooldown + holdings); the mod keeps no claim state.
        DailyCommand.WALLET.put(player.getUUID(), res.wallet());
        String tier = res.tier();
        try {
            Ranks.apply(player, tier);
            LOG.info("[ndrchst-auth] rank {} -> {}", tier,
                    player.getGameProfile().getName());
        } catch (Throwable t) {
            // FTB Ranks absent or command error — the gate still held; skip rank.
            LOG.warn("[ndrchst-auth] rank assign skipped: {}", t.toString());
        }
        try {
            MinecraftServer server = player.getServer();
            // Gamertag: tier color + glyph on the nameplate, tab list, and chat.
            TierTeams.assign(server, player, tier);
            TabList.apply(player);  // ambient $NDRCHST ticker in the tab menu
            player.sendSystemMessage(Flares.welcome(tier));
            // Bronze+ (a real holder, not the open base tier) gets announced.
            if (server != null && Tiers.rank(tier) >= 1) {
                server.getPlayerList().broadcastSystemMessage(
                        Flares.arrival(player.getGameProfile().getName(), tier), false);
            }
        } catch (Throwable t) {
            LOG.warn("[ndrchst-auth] tier surface skipped: {}", t.toString());
        }
    }

    private static void onRegisterPayloads(RegisterPayloadHandlersEvent event) {
        // NETWORK thread: the server handler does a blocking localhost verify;
        // keep it off the main game thread.
        PayloadRegistrar registrar = event.registrar("1").executesOn(HandlerThread.NETWORK);
        registrar.configurationToClient(
                JoinRequestPayload.TYPE, JoinRequestPayload.CODEC, NdrchstAuth::onClientRequest);
        registrar.configurationToServer(
                JoinResponsePayload.TYPE, JoinResponsePayload.CODEC, NdrchstAuth::onServerResponse);
    }

    private static void onRegisterConfigTasks(RegisterConfigurationTasksEvent event) {
        event.register(new JoinConfigTask());
    }

    /** Client side: read the client-written token and reply (empty if absent). */
    private static void onClientRequest(JoinRequestPayload payload, IPayloadContext ctx) {
        String token = "";
        try {
            Path p = FMLPaths.GAMEDIR.get().resolve(TOKEN_FILE);
            if (Files.exists(p)) {
                token = Files.readString(p).trim();
            }
        } catch (Exception e) {
            LOG.warn("[ndrchst-auth] could not read join token: {}", e.toString());
        }
        ctx.reply(new JoinResponsePayload(token));
    }

    /** Server side: verify the token with the box, gate the connection. */
    private static void onServerResponse(JoinResponsePayload payload, IPayloadContext ctx) {
        String username = null;
        UUID uuid = null;
        GameProfile profile = null;
        if (ctx.listener() instanceof ServerConfigurationPacketListenerImpl scpl) {
            profile = scpl.getOwner();
            if (profile != null) {
                username = profile.getName();
                uuid = profile.getId();
            }
        }

        JoinVerifier.Result res =
                payload.token().isBlank() ? null : JoinVerifier.verify(payload.token());
        if (res == null) {
            ctx.disconnect(Component.literal(
                    "ndrchst — sign in with your wallet in the launcher, then press Play."));
            return;
        }
        if (username != null && !res.mcName().equals(username)) {
            ctx.disconnect(Component.literal(
                    "ndrchst — that wallet plays as " + res.mcName() + ", not " + username + "."));
            return;
        }
        if (uuid != null) {
            VERIFIED.put(uuid, res);
        }
        // Apply the wallet's imported skin to the GameProfile NOW, during config —
        // the spawned ServerPlayer inherits it, so it renders for everyone with no
        // post-login refresh. Offline-mode servers can't fetch skins otherwise.
        if (profile != null && res.skinTexture() != null) {
            try {
                Skins.apply(profile, res.skinTexture(), res.skinModel());
            } catch (Throwable t) {
                LOG.warn("[ndrchst-auth] skin apply skipped: {}", t.toString());
            }
        }
        LOG.info("[ndrchst-auth] verified {} ({}) tier={} skin={}",
                username, res.wallet(), res.tier(), res.skinTexture() != null);
        ctx.finishCurrentTask(JoinConfigTask.TYPE);
    }
}

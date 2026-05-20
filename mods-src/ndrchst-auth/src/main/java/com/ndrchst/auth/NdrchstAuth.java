package com.ndrchst.auth;

import com.mojang.authlib.GameProfile;
import com.mojang.logging.LogUtils;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import net.minecraft.network.chat.Component;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
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
 * dir by the pilot). The client replies with it; the server verifies it via
 * {@link JoinVerifier} and either admits the player (stashing their wallet +
 * tier for rank assignment at login, M5) or disconnects. The payload channel
 * is non-optional, so a client without this mod is denied during negotiation —
 * the pilot is the only way in.
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
        modBus.addListener(NdrchstAuth::onRegisterPayloads);
        NeoForge.EVENT_BUS.addListener(NdrchstAuth::onRegisterConfigTasks);
        NeoForge.EVENT_BUS.addListener(NdrchstAuth::onPlayerLogin);
        LOG.info("[ndrchst-auth] loaded — wallet-gated join + holdings ranks");
    }

    /** Once the verified player is in, set their LuckPerms group from the tier. */
    private static void onPlayerLogin(PlayerEvent.PlayerLoggedInEvent event) {
        JoinVerifier.Result res = VERIFIED.remove(event.getEntity().getUUID());
        if (res == null || res.tier() == null) {
            return;
        }
        try {
            Ranks.apply(event.getEntity().getUUID(), res.tier());
            LOG.info("[ndrchst-auth] rank {} -> {}", res.tier(),
                    event.getEntity().getName().getString());
        } catch (Throwable t) {
            // LuckPerms absent or API error — the gate still held; skip rank.
            LOG.warn("[ndrchst-auth] rank assign skipped: {}", t.toString());
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

    /** Client side: read the pilot-written token and reply (empty if absent). */
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
        if (ctx.listener() instanceof ServerConfigurationPacketListenerImpl scpl) {
            GameProfile profile = scpl.getOwner();
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
        LOG.info("[ndrchst-auth] verified {} ({}) tier={}",
                username, res.wallet(), res.tier());
        ctx.finishCurrentTask(JoinConfigTask.TYPE);
    }
}

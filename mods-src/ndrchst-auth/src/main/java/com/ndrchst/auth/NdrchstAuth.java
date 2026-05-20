package com.ndrchst.auth;

import com.mojang.logging.LogUtils;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import org.slf4j.Logger;

/**
 * ndrchst wallet-auth mod.
 *
 * <p>The cryptographic gate for the ndrchst server: during the connection
 * configuration phase the client presents a short-lived join token (minted by
 * the box after a Sign-In-With-Solana), and the server verifies it via a
 * callback before letting the player in, then assigns their rank from the
 * wallet's holdings tier. A client without this mod (and a valid token) never
 * gets on — the pilot launcher is the only way in.
 *
 * <p>M1 is the skeleton: it loads on both sides and establishes the package.
 * The configuration-phase handshake + verify callback land in M4, ranks in M5.
 */
@Mod(NdrchstAuth.MOD_ID)
public final class NdrchstAuth {
    public static final String MOD_ID = "ndrchst_auth";
    private static final Logger LOG = LogUtils.getLogger();

    public NdrchstAuth(IEventBus modBus) {
        LOG.info("[ndrchst-auth] loaded — wallet-gated join + holdings ranks");
    }
}

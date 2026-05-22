package com.ndrchst.auth;

import com.mojang.authlib.GameProfile;
import com.mojang.authlib.properties.Property;
import com.mojang.authlib.properties.PropertyMap;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * Applies a wallet's imported skin to a player's {@link GameProfile} so it
 * renders in-game on this OFFLINE-mode server (which otherwise can't fetch
 * skins). The skin texture lives on Mojang's open CDN
 * ({@code textures.minecraft.net/texture/<hash>} — a host every client trusts),
 * so we just hand the client an (unsigned) {@code textures} property pointing at
 * it. Done during the configuration phase, before the player spawns, so the
 * spawned entity carries the skin with no post-login refresh.
 *
 * <p>Only IMPORTED skins (resolved from a Mojang username, so Mojang-hosted)
 * work this way; an uploaded custom PNG has no CDN hash and gets no in-game skin.
 */
final class Skins {
    private Skins() {}

    static void apply(GameProfile profile, String textureHash, String model) {
        if (textureHash == null || textureHash.isBlank()) {
            return;
        }
        String url = "http://textures.minecraft.net/texture/" + textureHash;
        String meta = "slim".equals(model) ? ",\"metadata\":{\"model\":\"slim\"}" : "";
        String json = "{\"textures\":{\"SKIN\":{\"url\":\"" + url + "\"" + meta + "}}}";
        String value = Base64.getEncoder()
                .encodeToString(json.getBytes(StandardCharsets.UTF_8));
        PropertyMap props = profile.getProperties();
        props.removeAll("textures");
        props.put("textures", new Property("textures", value));
    }
}

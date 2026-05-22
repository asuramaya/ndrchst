package com.ndrchst.core;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * The ONLINE-MODE (Paper / cross-play) gate client: maps a real, authenticated
 * MC identity to a wallet + tier via the box, and drives the in-game {@code
 * /link} flow. The modded path uses {@link JoinVerifier} (signed join token)
 * instead; both hit the same box and the same tier source — this is just the
 * second trigger. Same HTTP/1.1 + bridge-gated pattern as the other clients;
 * returns null / {@code ok=false} on failure (caller decides deny vs. retry).
 */
public final class IdentityGateClient {
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    /** ok=false means unlinked (kick/limbo with a link prompt); tier is the
     *  rank when ok. wallet is the bound pubkey (null when unlinked). */
    public record GateResult(boolean ok, String wallet, String tier, String reason) {}

    /** A pending /link: show userCode + verifyUrl to the player, poll pairId. */
    public record LinkStart(String pairId, String userCode, String verifyUrl) {}

    /** Poll result: status is "pending" or "approved"; tier/wallet set once approved. */
    public record LinkStatus(String status, String wallet, String tier) {}

    private IdentityGateClient() {}

    private static String base() {
        String env = System.getenv("NDRCHST_BOX_BASE");
        if (env == null || env.isBlank()) {
            env = System.getenv("NDRCHST_DAILY_BASE");  // shared box-base convention
        }
        return (env != null && !env.isBlank()) ? env : "http://172.17.0.1:8081";
    }

    /** Pre-login gate. ok=false (not null) means a clean "unlinked" deny; null
     *  means the box was unreachable (caller may fail-open or retry). */
    public static GateResult gate(String uuid, String xuid, String username) {
        JsonObject body = new JsonObject();
        body.addProperty("uuid", uuid);
        if (xuid != null) {
            body.addProperty("xuid", xuid);
        }
        if (username != null) {
            body.addProperty("username", username);
        }
        JsonObject o = post("/gate/identity", body);
        if (o == null) {
            return null;
        }
        boolean ok = o.has("ok") && o.get("ok").getAsBoolean();
        if (!ok) {
            String reason = (o.has("reason") && !o.get("reason").isJsonNull())
                    ? o.get("reason").getAsString() : "unlinked";
            return new GateResult(false, null, null, reason);
        }
        String wallet = o.get("wallet").getAsString();
        String tier = (o.has("tier") && !o.get("tier").isJsonNull())
                ? o.get("tier").getAsString() : "holder";
        return new GateResult(true, wallet, tier, null);
    }

    /** Begin an in-game /link for the player's authenticated identity. */
    public static LinkStart linkStart(String uuid, String xuid, String username) {
        JsonObject body = new JsonObject();
        body.addProperty("uuid", uuid);
        if (xuid != null) {
            body.addProperty("xuid", xuid);
        }
        if (username != null) {
            body.addProperty("username", username);
        }
        JsonObject o = post("/gate/link/start", body);
        if (o == null || !o.has("user_code")) {
            return null;
        }
        return new LinkStart(
                o.get("pair_id").getAsString(),
                o.get("user_code").getAsString(),
                o.get("verify_url").getAsString());
    }

    /** Poll a pending /link; status "pending" until the player approves. */
    public static LinkStatus linkPoll(String pairId) {
        try {
            String url = base() + "/gate/link/poll?pair_id="
                    + URLEncoder.encode(pairId, StandardCharsets.UTF_8);
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .timeout(Duration.ofSeconds(8))
                    .header("user-agent", "ndrchst-paper")
                    .GET()
                    .build();
            HttpResponse<String> resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                return null;
            }
            JsonObject o = JsonParser.parseString(resp.body()).getAsJsonObject();
            String status = o.has("status") ? o.get("status").getAsString() : "pending";
            String wallet = (o.has("wallet") && !o.get("wallet").isJsonNull())
                    ? o.get("wallet").getAsString() : null;
            String tier = (o.has("tier") && !o.get("tier").isJsonNull())
                    ? o.get("tier").getAsString() : null;
            return new LinkStatus(status, wallet, tier);
        } catch (Exception e) {
            return null;
        }
    }

    private static JsonObject post(String path, JsonObject body) {
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(base() + path))
                    .timeout(Duration.ofSeconds(8))
                    .header("content-type", "application/json")
                    .header("user-agent", "ndrchst-paper")
                    .POST(HttpRequest.BodyPublishers.ofString(body.toString()))
                    .build();
            HttpResponse<String> resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                return null;
            }
            return JsonParser.parseString(resp.body()).getAsJsonObject();
        } catch (Exception e) {
            return null;
        }
    }
}

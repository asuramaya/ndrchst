package com.ndrchst.core;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Calls the box's daily endpoints. The box is authoritative for the 24h
 * cooldown (durable across server restarts, keyed by wallet) and returns the
 * reward tier from the hourly holdings snapshot. Same HTTP/1.1 trick as
 * {@link JoinVerifier} (the default HttpClient's h2c upgrade breaks uvicorn on
 * plaintext http://). Returns null on any network failure — the caller treats
 * that as "couldn't claim, try again".
 *
 * Endpoints are reachable only from the box's container network (the public
 * surface gates them to the Docker bridge), so the wallet we pass is trusted.
 * Default base is the bridge gateway, overridable via {@code NDRCHST_DAILY_BASE}.
 */
public final class DailyClient {
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    public record ClaimResult(boolean ok, String tier, int secondsLeft) {}

    private DailyClient() {}

    private static String base() {
        String env = System.getenv("NDRCHST_DAILY_BASE");
        return (env != null && !env.isBlank()) ? env : "http://172.17.0.1:8081";
    }

    /** Claim the daily for a wallet. null on network failure. */
    public static ClaimResult claim(String wallet) {
        JsonObject body = new JsonObject();
        body.addProperty("wallet", wallet);
        JsonObject o = post("/daily/claim", body);
        if (o == null) {
            return null;
        }
        boolean ok = o.has("ok") && o.get("ok").getAsBoolean();
        String tier = (o.has("tier") && !o.get("tier").isJsonNull())
                ? o.get("tier").getAsString() : "holder";
        int left = o.has("seconds_left") ? o.get("seconds_left").getAsInt() : 0;
        return new ClaimResult(ok, tier, left);
    }

    /** Clear a wallet's cooldown (op escape hatch). false on failure. */
    public static boolean reset(String wallet) {
        JsonObject body = new JsonObject();
        body.addProperty("wallet", wallet);
        JsonObject o = post("/daily/reset", body);
        return o != null && o.has("ok") && o.get("ok").getAsBoolean();
    }

    private static JsonObject post(String path, JsonObject body) {
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(base() + path))
                    .timeout(Duration.ofSeconds(8))
                    .header("content-type", "application/json")
                    .header("user-agent", "ndrchst-auth")
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

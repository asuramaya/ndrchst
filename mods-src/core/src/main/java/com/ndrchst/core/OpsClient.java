package com.ndrchst.core;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Reads + sets the box's operator-tunable runtime knobs (the daily cooldown and
 * the refresh cadences) for the in-game {@code /ndrchst config} surface. Same
 * HTTP/1.1 + bridge-gated pattern as {@link DailyClient}; the box clamps +
 * persists, so the mod just relays. Returns null on any failure.
 */
public final class OpsClient {
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    private OpsClient() {}

    private static String base() {
        String env = System.getenv("NDRCHST_DAILY_BASE");
        return (env != null && !env.isBlank()) ? env : "http://172.17.0.1:8081";
    }

    /** Current effective knob values (insertion-ordered), or null on failure. */
    public static Map<String, Integer> listConfig() {
        JsonObject o = post("/ops/config", new JsonObject());
        if (o == null || !o.get("config").isJsonObject()) {
            return null;
        }
        Map<String, Integer> out = new LinkedHashMap<>();
        for (var e : o.getAsJsonObject("config").entrySet()) {
            out.put(e.getKey(), e.getValue().getAsInt());
        }
        return out;
    }

    /** Set a knob; returns the value the box actually applied (post-clamp), or
     *  null on failure (incl. an unknown knob → 400). */
    public static Integer setConfig(String key, int value) {
        JsonObject body = new JsonObject();
        body.addProperty("key", key);
        body.addProperty("value", value);
        JsonObject o = post("/ops/config/set", body);
        if (o == null || !o.has("value")) {
            return null;
        }
        return o.get("value").getAsInt();
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
            JsonObject o = JsonParser.parseString(resp.body()).getAsJsonObject();
            return (o.has("ok") && o.get("ok").getAsBoolean()) ? o : null;
        } catch (Exception e) {
            return null;
        }
    }
}

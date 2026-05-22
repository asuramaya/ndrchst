package com.ndrchst.auth;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Asks the box for a wallet's standing for the {@code /tier} readout: current
 * tier, holdings %, and the next rung's threshold. A pure DB read on the box
 * (no live RPC), so it costs nothing against the metered Solana cap and is safe
 * to call on demand. Same HTTP/1.1 + bridge-gated pattern as {@link DailyClient}
 * / {@link JoinVerifier}; returns null on any failure (caller shows a retry).
 */
public final class TierClient {
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    /** nextKey/nextName null + nextPct 0 means the wallet is at the top tier.
     *  priceUsd is NaN when the box has no cached ticker (decoration absent). */
    public record Standing(double pct, String tier, String nextKey, String nextName,
                           double nextPct, double priceUsd, double marketCap) {}

    /** Bare cached ticker for the tab menu; marketCap NaN if unknown. */
    public record Price(double usd, double marketCap) {}

    private TierClient() {}

    private static String base() {
        String env = System.getenv("NDRCHST_DAILY_BASE");
        return (env != null && !env.isBlank()) ? env : "http://172.17.0.1:8081";
    }

    /** Look up a wallet's standing. null on network/parse failure. */
    public static Standing lookup(String wallet) {
        try {
            JsonObject body = new JsonObject();
            body.addProperty("wallet", wallet);
            HttpRequest req = HttpRequest.newBuilder(URI.create(base() + "/tier"))
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
            if (!o.has("ok") || !o.get("ok").getAsBoolean()) {
                return null;
            }
            double pct = o.has("pct") ? o.get("pct").getAsDouble() : 0.0;
            String tier = (o.has("tier") && !o.get("tier").isJsonNull())
                    ? o.get("tier").getAsString() : "holder";
            String nextKey = null, nextName = null;
            double nextPct = 0.0;
            if (o.has("next") && o.get("next").isJsonObject()) {
                JsonObject n = o.getAsJsonObject("next");
                nextKey = n.get("key").getAsString();
                nextName = n.get("name").getAsString();
                nextPct = n.get("min_pct").getAsDouble();
            }
            double priceUsd = Double.NaN, marketCap = Double.NaN;
            if (o.has("price") && o.get("price").isJsonObject()) {
                JsonObject pr = o.getAsJsonObject("price");
                if (pr.has("usd") && !pr.get("usd").isJsonNull()) {
                    priceUsd = pr.get("usd").getAsDouble();
                }
                if (pr.has("market_cap") && !pr.get("market_cap").isJsonNull()) {
                    marketCap = pr.get("market_cap").getAsDouble();
                }
            }
            return new Standing(pct, tier, nextKey, nextName, nextPct, priceUsd, marketCap);
        } catch (Exception e) {
            return null;
        }
    }

    /** Fetch the box's cached ticker (GET /price) for the tab menu. null on any
     *  failure or when nothing is cached. */
    public static Price price() {
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(base() + "/price"))
                    .timeout(Duration.ofSeconds(8))
                    .header("user-agent", "ndrchst-auth")
                    .GET()
                    .build();
            HttpResponse<String> resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                return null;
            }
            JsonObject o = JsonParser.parseString(resp.body()).getAsJsonObject();
            if (!o.has("price") || o.get("price").isJsonNull()) {
                return null;
            }
            JsonObject p = o.getAsJsonObject("price");
            double usd = (p.has("usd") && !p.get("usd").isJsonNull())
                    ? p.get("usd").getAsDouble() : Double.NaN;
            if (Double.isNaN(usd) || usd <= 0) {
                return null;
            }
            double mc = (p.has("market_cap") && !p.get("market_cap").isJsonNull())
                    ? p.get("market_cap").getAsDouble() : Double.NaN;
            return new Price(usd, mc);
        } catch (Exception e) {
            return null;
        }
    }
}

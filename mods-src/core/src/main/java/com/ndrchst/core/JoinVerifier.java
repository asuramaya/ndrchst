package com.ndrchst.core;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Calls the box's {@code /join/verify} to validate a wallet join token. The
 * box is the trust root: it minted the token (HMAC) after a real wallet
 * signature and re-reads the holdings tier here. Returns null on any failure
 * (which the caller treats as "reject the connection").
 *
 * The MC server runs in a Docker container; the box public app is on the host.
 * Default target is the Docker bridge gateway ({@code 172.17.0.1:8081}),
 * overridable via {@code NDRCHST_JOIN_VERIFY_URL}.
 */
public final class JoinVerifier {
    // Force HTTP/1.1: the default HttpClient tries an HTTP/2 (h2c) upgrade on
    // plaintext http://, which uvicorn doesn't support — it drops the request
    // body, so /join/verify saw no body and 422'd (rejecting every join).
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    public record Result(boolean ok, String wallet, String mcName, String tier,
                         String skinTexture, String skinModel) {}

    private JoinVerifier() {}

    private static String verifyUrl() {
        String env = System.getenv("NDRCHST_JOIN_VERIFY_URL");
        return (env != null && !env.isBlank())
                ? env
                : "http://172.17.0.1:8081/join/verify";
    }

    public static Result verify(String token) {
        try {
            JsonObject req = new JsonObject();
            req.addProperty("token", token);
            HttpRequest httpReq = HttpRequest.newBuilder(URI.create(verifyUrl()))
                    .timeout(Duration.ofSeconds(8))
                    .header("content-type", "application/json")
                    .header("user-agent", "ndrchst-auth")
                    .POST(HttpRequest.BodyPublishers.ofString(req.toString()))
                    .build();
            HttpResponse<String> resp = HTTP.send(httpReq, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                return null;
            }
            JsonObject o = JsonParser.parseString(resp.body()).getAsJsonObject();
            if (!o.has("ok") || !o.get("ok").getAsBoolean()) {
                return null;
            }
            String tier = (o.has("tier") && !o.get("tier").isJsonNull())
                    ? o.get("tier").getAsString() : null;
            String skinTex = null;
            String skinModel = "classic";
            if (o.has("skin") && o.get("skin").isJsonObject()) {
                JsonObject sk = o.getAsJsonObject("skin");
                if (sk.has("texture") && !sk.get("texture").isJsonNull()) {
                    skinTex = sk.get("texture").getAsString();
                }
                if (sk.has("model") && !sk.get("model").isJsonNull()) {
                    skinModel = sk.get("model").getAsString();
                }
            }
            return new Result(
                    true,
                    o.get("wallet").getAsString(),
                    o.get("mc_name").getAsString(),
                    tier,
                    skinTex,
                    skinModel);
        } catch (Exception e) {
            return null;
        }
    }
}

package com.ndrchst.paper;

import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-session cache of a player's bound wallet + tier, populated by the gate
 * (pre-login) or a successful {@code /link}. In-memory only — the durable
 * binding lives on the box (identity_links); this just avoids re-hitting the
 * box on every command. Cleared on quit.
 */
final class WalletRegistry {
    private final ConcurrentHashMap<UUID, String> wallet = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<UUID, String> tier = new ConcurrentHashMap<>();

    void set(UUID id, String w, String t) {
        if (w != null) {
            wallet.put(id, w);
        }
        tier.put(id, t == null ? "holder" : t);
    }

    void clear(UUID id) {
        wallet.remove(id);
        tier.remove(id);
    }

    String wallet(UUID id) {
        return wallet.get(id);
    }

    String tier(UUID id) {
        return tier.getOrDefault(id, "holder");
    }

    boolean linked(UUID id) {
        return wallet.containsKey(id);
    }
}

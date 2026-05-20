package com.ndrchst.auth;

import java.util.UUID;
import net.luckperms.api.LuckPerms;
import net.luckperms.api.LuckPermsProvider;
import net.luckperms.api.node.NodeType;
import net.luckperms.api.node.types.InheritanceNode;

/**
 * Sets a player's LuckPerms group from their holdings tier. The tier key
 * (holder/bronze/silver/gold/diamond/whale) is used directly as the group
 * name — those groups must exist on the server (created in M6).
 *
 * LuckPerms references are isolated here so the caller can guard the whole
 * thing in a try/catch(Throwable): if the LuckPerms mod isn't installed, this
 * class fails to link and the gate still works — only the rank is skipped.
 */
final class Ranks {
    private Ranks() {}

    static void apply(UUID uuid, String tier) {
        LuckPerms lp = LuckPermsProvider.get();
        lp.getUserManager().modifyUser(uuid, user -> {
            // One group at a time: drop existing inheritance, set the tier group.
            user.data().clear(NodeType.INHERITANCE::matches);
            user.data().add(InheritanceNode.builder(tier).build());
        });
    }
}

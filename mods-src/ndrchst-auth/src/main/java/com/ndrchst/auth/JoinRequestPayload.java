package com.ndrchst.auth;

import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;

/** Server → client, configuration phase: "present your wallet join token". */
public record JoinRequestPayload(int protocol) implements CustomPacketPayload {
    public static final Type<JoinRequestPayload> TYPE = new Type<>(
            ResourceLocation.fromNamespaceAndPath(NdrchstAuth.MOD_ID, "join_request"));

    public static final StreamCodec<FriendlyByteBuf, JoinRequestPayload> CODEC =
            StreamCodec.composite(
                    ByteBufCodecs.VAR_INT, JoinRequestPayload::protocol,
                    JoinRequestPayload::new);

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}

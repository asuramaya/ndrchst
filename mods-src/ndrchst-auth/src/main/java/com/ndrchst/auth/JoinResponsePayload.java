package com.ndrchst.auth;

import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;

/** Client → server, configuration phase: the wallet join token. */
public record JoinResponsePayload(String token) implements CustomPacketPayload {
    public static final Type<JoinResponsePayload> TYPE = new Type<>(
            ResourceLocation.fromNamespaceAndPath(NdrchstAuth.MOD_ID, "join_response"));

    public static final StreamCodec<FriendlyByteBuf, JoinResponsePayload> CODEC =
            StreamCodec.composite(
                    ByteBufCodecs.STRING_UTF8, JoinResponsePayload::token,
                    JoinResponsePayload::new);

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}

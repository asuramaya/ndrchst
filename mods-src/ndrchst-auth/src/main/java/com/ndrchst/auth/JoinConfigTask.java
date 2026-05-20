package com.ndrchst.auth;

import java.util.function.Consumer;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.server.network.ConfigurationTask;
import net.neoforged.neoforge.network.configuration.ICustomConfigurationTask;

/**
 * Runs once per connecting client during the configuration phase: sends the
 * "present your token" request. The phase blocks until the server's response
 * handler calls {@code finishCurrentTask(TYPE)} (admit) or disconnects.
 */
public record JoinConfigTask() implements ICustomConfigurationTask {
    public static final ConfigurationTask.Type TYPE =
            new ConfigurationTask.Type(NdrchstAuth.MOD_ID + ":join");

    @Override
    public void run(Consumer<CustomPacketPayload> sender) {
        sender.accept(new JoinRequestPayload(1));
    }

    @Override
    public ConfigurationTask.Type type() {
        return TYPE;
    }
}

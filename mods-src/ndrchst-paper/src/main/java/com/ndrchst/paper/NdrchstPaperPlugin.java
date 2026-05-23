package com.ndrchst.paper;

import org.bukkit.plugin.java.JavaPlugin;

/**
 * Cross-play (Paper) entry point. The wallet brain stays on the box and the
 * shared {@code com.ndrchst.core} client/tier model is the same code the
 * NeoForge mod uses — this plugin is only the online-mode adapter: gate joins
 * on the player's authenticated identity, run {@code /link}, and render tiers.
 */
public final class NdrchstPaperPlugin extends JavaPlugin {
    private final WalletRegistry wallets = new WalletRegistry();

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(new GateListener(this, wallets), this);
        getServer().getPluginManager().registerEvents(new ChatListener(wallets), this);
        getCommand("link").setExecutor(new LinkCommand(this, wallets));
        InfoCommands info = new InfoCommands(this, wallets);
        getCommand("tier").setExecutor(info);
        getCommand("price").setExecutor(info);
        PerkCommands perks = new PerkCommands();
        getCommand("fly").setExecutor(perks);
        getCommand("hat").setExecutor(perks);
        getLogger().info("ndrchst-paper enabled — online-mode wallet gate + $NDRCHST tiers + perks");
    }
}

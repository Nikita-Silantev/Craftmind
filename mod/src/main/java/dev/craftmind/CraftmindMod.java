package dev.craftmind;

import com.mojang.brigadier.CommandDispatcher;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class CraftmindMod implements ModInitializer {
    public static final String MOD_ID = "craftmind";
    private static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    @Override
    public void onInitialize() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> registerCommands(dispatcher));
        LOGGER.info("Craftmind initialized");
    }

    private static void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("craftmind")
            .then(Commands.literal("status").executes(context -> {
                context.getSource().sendSuccess(() -> Component.literal("Craftmind is running (prototype 0.1.0)"), false);
                return 1;
            })));
    }
}

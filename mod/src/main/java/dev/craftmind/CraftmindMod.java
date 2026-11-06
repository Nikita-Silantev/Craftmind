package dev.craftmind;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.mojang.brigadier.CommandDispatcher;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.message.v1.ServerMessageEvents;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.PermissionSet;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.TimeUnit;

public final class CraftmindMod implements ModInitializer {
    public static final String MOD_ID = "craftmind";
    private static final String BOT_NAME = "Craftmind";
    private static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);
    private static final Gson GSON = new Gson();
    private final ConcurrentLinkedQueue<Map<String, String>> events = new ConcurrentLinkedQueue<>();
    private HttpServer bridge;
    private MinecraftServer server;
    private long ticks;
    private long stopAtTick;

    @Override
    public void onInitialize() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> registerCommands(dispatcher));
        ServerMessageEvents.CHAT_MESSAGE.register((message, sender, params) -> {
            String content = message.signedContent().trim();
            if (content.regionMatches(true, 0, "@" + BOT_NAME + " ", 0, BOT_NAME.length() + 2)) {
                String task = content.substring(BOT_NAME.length() + 2).trim();
                if (!task.isEmpty()) {
                    events.add(Map.of("player", sender.getGameProfile().name(), "task", task));
                    sender.sendSystemMessage(Component.literal("[Craftmind] Задача получена: " + task));
                }
            }
        });
        ServerTickEvents.END_SERVER_TICK.register(s -> {
            ticks++;
            if (stopAtTick > 0 && ticks >= stopAtTick) {
                stopAtTick = 0;
                ServerPlayer bot = s.getPlayerList().getPlayerByName(BOT_NAME);
                if (bot != null) runCarpet(bot.createCommandSourceStack(), "player " + BOT_NAME + " stop");
            }
        });
        ServerLifecycleEvents.SERVER_STARTED.register(this::startBridge);
        ServerLifecycleEvents.SERVER_STOPPING.register(s -> stopBridge());
        LOGGER.info("Craftmind initialized");
    }

    private void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("craftmind")
            .then(Commands.literal("status").executes(ctx -> {
                boolean online = bridge != null;
                ctx.getSource().sendSuccess(() -> Component.literal("Craftmind bridge: " + (online ? "127.0.0.1:8765" : "stopped")), false);
                return 1;
            }))
            .then(Commands.literal("spawn").executes(ctx -> {
                if (ctx.getSource().getPlayer() == null) return 0;
                if (server.getPlayerList().getPlayerByName(BOT_NAME) != null) {
                    ctx.getSource().sendFailure(Component.literal("Craftmind already exists"));
                    return 0;
                }
                int result = runCarpet(ctx.getSource(), "player " + BOT_NAME + " spawn");
                if (result == 0) ctx.getSource().sendFailure(Component.literal("Install Fabric Carpet and enable /player"));
                return result;
            })));
    }

    private int runCarpet(CommandSourceStack source, String command) {
        if (server.getCommands().getDispatcher().getRoot().getChild("player") == null) return 0;
        server.getCommands().performPrefixedCommand(source.withPermission(PermissionSet.ALL_PERMISSIONS), command);
        return 1;
    }

    private void startBridge(MinecraftServer minecraftServer) {
        this.server = minecraftServer;
        try {
            bridge = HttpServer.create(new InetSocketAddress("127.0.0.1", 8765), 0);
            bridge.createContext("/events", this::handleEvents);
            bridge.createContext("/state", this::handleState);
            bridge.createContext("/action", this::handleAction);
            bridge.createContext("/reply", this::handleReply);
            bridge.setExecutor(java.util.concurrent.Executors.newCachedThreadPool());
            bridge.start();
            LOGGER.info("Craftmind bridge listening on 127.0.0.1:8765");
        } catch (IOException e) {
            LOGGER.error("Cannot start Craftmind bridge", e);
        }
    }

    private void stopBridge() {
        if (bridge != null) bridge.stop(0);
        bridge = null;
        server = null;
        events.clear();
    }

    private void handleEvents(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("GET")) { respond(exchange, 405, Map.of("error", "GET required")); return; }
        List<Map<String, String>> batch = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            Map<String, String> event = events.poll();
            if (event == null) break;
            batch.add(event);
        }
        respond(exchange, 200, Map.of("events", batch));
    }

    private void handleState(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("GET")) { respond(exchange, 405, Map.of("error", "GET required")); return; }
        try {
            Map<String, Object> state = server.submit(() -> {
                ServerPlayer bot = server.getPlayerList().getPlayerByName(BOT_NAME);
                if (bot == null) return Map.<String, Object>of("bot", "absent");
                return Map.<String, Object>of("bot", "present", "x", bot.getX(), "y", bot.getY(), "z", bot.getZ(), "health", bot.getHealth());
            }).get(3, TimeUnit.SECONDS);
            respond(exchange, 200, state);
        } catch (Exception e) { respond(exchange, 500, Map.of("error", e.toString())); }
    }

    private void handleAction(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("POST")) { respond(exchange, 405, Map.of("error", "POST required")); return; }
        byte[] body = exchange.getRequestBody().readNBytes(4096);
        try {
            JsonObject input = GSON.fromJson(new String(body, StandardCharsets.UTF_8), JsonObject.class);
            String action = input.get("action").getAsString();
            if (!List.of("forward", "back", "left", "right", "stop", "jump").contains(action)) {
                respond(exchange, 400, Map.of("error", "unknown action")); return;
            }
            int result = server.submit(() -> {
                ServerPlayer bot = server.getPlayerList().getPlayerByName(BOT_NAME);
                if (bot == null) return 0;
                int applied = runCarpet(bot.createCommandSourceStack(), "player " + BOT_NAME + " " + (action.equals("jump") ? "jump once" : action.equals("stop") ? "stop" : "move " + (action.equals("back") ? "backward" : action)));
                stopAtTick = List.of("forward", "back", "left", "right").contains(action) ? ticks + 40 : 0;
                return applied;
            }).get(3, TimeUnit.SECONDS);
            respond(exchange, result > 0 ? 200 : 409, Map.of("ok", result > 0));
        } catch (Exception e) { respond(exchange, 400, Map.of("error", e.toString())); }
    }

    private void handleReply(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("POST")) { respond(exchange, 405, Map.of("error", "POST required")); return; }
        try {
            JsonObject input = GSON.fromJson(new String(exchange.getRequestBody().readNBytes(1024), StandardCharsets.UTF_8), JsonObject.class);
            String text = input.get("text").getAsString().replace('\n', ' ').trim();
            if (text.isEmpty() || text.length() > 200) { respond(exchange, 400, Map.of("error", "invalid reply")); return; }
            server.submit(() -> server.getPlayerList().broadcastSystemMessage(Component.literal("[Craftmind] " + text), false)).get(3, TimeUnit.SECONDS);
            respond(exchange, 200, Map.of("ok", true));
        } catch (Exception e) { respond(exchange, 400, Map.of("error", e.toString())); }
    }

    private static void respond(HttpExchange exchange, int status, Object value) throws IOException {
        byte[] body = GSON.toJson(value).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        try (var out = exchange.getResponseBody()) { out.write(body); }
    }
}
